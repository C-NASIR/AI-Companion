"""Model streaming adapters for Session 0 backend."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import AsyncGenerator, Mapping, Sequence
from openai import AsyncOpenAI

from .schemas import ChatMode
from .observability.costs import estimate_cost_usd
from .models import ModelCapability, get_model_router


def get_openai_api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY")


def get_openai_base_url() -> str | None:
    return os.getenv("OPENAI_BASE_URL")


def load_environment() -> None:
    """Explicitly load environment variables from .env (if available)."""

    from .env import load_dotenv_if_present

    load_dotenv_if_present()


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    api_key = get_openai_api_key()
    base_url = get_openai_base_url()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing")
    global _client
    if _client is None:
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        _client = AsyncOpenAI(**client_kwargs)
    return _client


def _value_from_chunk(chunk: Mapping[str, object] | object, key: str) -> object | None:
    if isinstance(chunk, Mapping):
        return chunk.get(key)
    return getattr(chunk, key, None)


def _format_evidence_message(
    retrieved_chunks: Sequence[Mapping[str, object] | object],
) -> str:
    if not retrieved_chunks:
        return (
            "No evidence chunks are available. "
            'Respond with the exact sentence "I lack sufficient evidence to answer."'
        )
    lines: list[str] = []
    for idx, chunk in enumerate(retrieved_chunks, start=1):
        chunk_id = _value_from_chunk(chunk, "chunk_id") or f"chunk_{idx}"
        text = _value_from_chunk(chunk, "text") or ""
        text_snippet = str(text).strip()
        lines.append(f"{idx}. [{chunk_id}] {text_snippet}")
    intro = (
        "Ground your answer only in the evidence chunks below. "
        "Cite chunk ids inline like [chunk_id]."
    )
    return intro + "\n" + "\n".join(lines)


def _approximate_tokens(char_count: int) -> int:
    if char_count <= 0:
        return 0
    return max(char_count // 4, 1)


@dataclass
class ModelInvocationMetrics:
    """Holds usage metadata for a single model invocation."""

    model_name: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    input_char_count: int = 0
    output_char_count: int = 0

    def record_prompt_chars(self, chars: int) -> None:
        if chars > 0:
            self.input_char_count += chars

    def record_completion_chars(self, chars: int) -> None:
        if chars > 0:
            self.output_char_count += chars

    def ensure_estimates(self) -> None:
        if self.input_tokens <= 0 and self.input_char_count:
            self.input_tokens = _approximate_tokens(self.input_char_count)
        if self.output_tokens <= 0 and self.output_char_count:
            self.output_tokens = _approximate_tokens(self.output_char_count)

    def estimated_cost_usd(self) -> float:
        self.ensure_estimates()
        return estimate_cost_usd(self.model_name, self.input_tokens, self.output_tokens)


async def real_stream(
    message: str,
    context: str | None,
    mode: ChatMode,
    run_id: str,
    retrieved_chunks: Sequence[Mapping[str, object] | object],
    *,
    is_evaluation: bool = False,
    capability: ModelCapability,
    metrics: ModelInvocationMetrics | None = None,
) -> AsyncGenerator[str, None]:
    """Stream completion chunks from OpenAI."""
    client = _get_client()
    model_router = get_model_router()
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are an AI companion focused on clarity. "
                f"Operate in {mode.value} mode and keep reasoning visible."
            ),
        }
    ]
    messages.append(
        {
            "role": "system",
            "content": _format_evidence_message(retrieved_chunks),
        }
    )
    if context:
        messages.append(
            {
                "role": "user",
                "content": f"Context for reference:\n{context}",
            }
        )
    messages.append({"role": "user", "content": message})
    completion_kwargs: dict[str, object] = {
        "model": model_router.route(capability),
        "messages": messages,
        "stream": True,
    }
    prompt_chars = sum(len(str(item.get("content", ""))) for item in messages)
    model_name = completion_kwargs["model"]
    if metrics:
        metrics.model_name = model_name
        metrics.record_prompt_chars(prompt_chars)
    if is_evaluation:
        completion_kwargs["temperature"] = 0
        completion_kwargs["top_p"] = 1
    stream = await client.chat.completions.create(**completion_kwargs)
    async for event in stream:
        choice = event.choices[0]
        delta = choice.delta
        content = delta.content
        usage = getattr(event, "usage", None)
        if metrics and usage:
            prompt_tokens = getattr(usage, "prompt_tokens", None)
            completion_tokens = getattr(usage, "completion_tokens", None)
            if prompt_tokens is not None:
                metrics.input_tokens = int(prompt_tokens)
            if completion_tokens is not None:
                metrics.output_tokens = int(completion_tokens)
        if not content:
            continue
        if isinstance(content, str):
            if metrics:
                metrics.record_completion_chars(len(content))
            yield content
        else:
            for fragment in content:
                text = getattr(fragment, "text", None)
                if text:
                    if metrics:
                        metrics.record_completion_chars(len(text))
                    yield text


async def fake_stream(
    message: str,
    context: str | None,
    mode: ChatMode,
    run_id: str,
    retrieved_chunks: Sequence[Mapping[str, object] | object],
    *,
    is_evaluation: bool = False,
    capability: ModelCapability,
    metrics: ModelInvocationMetrics | None = None,
) -> AsyncGenerator[str, None]:
    """Local deterministic stream when OpenAI credentials are unavailable."""
    snippet = (message.strip() or "…")[:60]
    context_snippet = (context.strip() if context else "none provided")[:80]
    if metrics:
        metrics.model_name = model_router.route(capability)
        metrics.record_prompt_chars(len(message) + len(context or ""))
        chunk_chars = sum(len(str(_value_from_chunk(chunk, "text") or "")) for chunk in retrieved_chunks)
        metrics.record_prompt_chars(chunk_chars)
    if retrieved_chunks:
        evidence_sentences: list[str] = []
        for chunk in retrieved_chunks:
            chunk_id = _value_from_chunk(chunk, "chunk_id") or "chunk"
            text = str(_value_from_chunk(chunk, "text") or "").strip().replace("\n", " ")
            snippet_text = text[:120]
            evidence_sentences.append(f"[{chunk_id}] {snippet_text}")
        answer_body = " ".join(evidence_sentences)
        chunks = [
            f"(fake:{run_id}) Mode={mode.value}. ",
            "Grounded response using retrieved evidence. ",
            "User message snippet: ",
            snippet,
            ". ",
            "Context snippet: ",
            context_snippet,
            ". ",
            answer_body,
            " Replace OPENAI_API_KEY to enable live streaming.",
        ]
    else:
        chunks = [
            f"(fake:{run_id}) Mode={mode.value}. ",
            "No evidence chunks were retrieved. ",
            'I lack sufficient evidence to answer.',
            " Replace OPENAI_API_KEY to enable live streaming.",
        ]
    for chunk in chunks:
        if metrics:
            metrics.record_completion_chars(len(chunk))
        await asyncio.sleep(0.15)
        yield chunk


async def stream_chat(
    message: str,
    context: str | None,
    mode: ChatMode,
    run_id: str,
    retrieved_chunks: Sequence[Mapping[str, object] | object] | None = None,
    *,
    is_evaluation: bool = False,
    capability: ModelCapability,
    metrics: ModelInvocationMetrics | None = None,
) -> AsyncIterator[str]:
    """Dispatch to real or fake streamer."""
    evidence = retrieved_chunks or []
    if get_openai_api_key():
        async for chunk in real_stream(
            message,
            context,
            mode,
            run_id,
            evidence,
            is_evaluation=is_evaluation,
            capability=capability,
            metrics=metrics,
        ):
            yield chunk
    else:
        async for chunk in fake_stream(
            message,
            context,
            mode,
            run_id,
            evidence,
            is_evaluation=is_evaluation,
            capability=capability,
            metrics=metrics,
        ):
            yield chunk
