"""Model streaming adapters for Session 0 backend."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import AsyncGenerator, Mapping, Sequence

from dotenv import find_dotenv, load_dotenv
from openai import AsyncOpenAI

from .schemas import ChatMode

_DOTENV_PATH = find_dotenv(filename=".env", usecwd=True)

if _DOTENV_PATH:
    load_dotenv(dotenv_path=_DOTENV_PATH)
else:
    load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")
    global _client
    if _client is None:
        client_kwargs = {"api_key": OPENAI_API_KEY}
        if OPENAI_BASE_URL:
            client_kwargs["base_url"] = OPENAI_BASE_URL
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


async def real_stream(
    message: str,
    context: str | None,
    mode: ChatMode,
    run_id: str,
    retrieved_chunks: Sequence[Mapping[str, object] | object],
    *,
    is_evaluation: bool = False,
) -> AsyncGenerator[str, None]:
    """Stream completion chunks from OpenAI."""
    client = _get_client()
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
        "model": OPENAI_MODEL,
        "messages": messages,
        "stream": True,
    }
    if is_evaluation:
        completion_kwargs["temperature"] = 0
        completion_kwargs["top_p"] = 1
    stream = await client.chat.completions.create(**completion_kwargs)
    async for event in stream:
        choice = event.choices[0]
        delta = choice.delta
        content = delta.content
        if not content:
            continue
        if isinstance(content, str):
            yield content
        else:
            for fragment in content:
                text = getattr(fragment, "text", None)
                if text:
                    yield text


async def fake_stream(
    message: str,
    context: str | None,
    mode: ChatMode,
    run_id: str,
    retrieved_chunks: Sequence[Mapping[str, object] | object],
    *,
    is_evaluation: bool = False,
) -> AsyncGenerator[str, None]:
    """Local deterministic stream when OpenAI credentials are unavailable."""
    snippet = (message.strip() or "…")[:60]
    context_snippet = (context.strip() if context else "none provided")[:80]
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
) -> AsyncIterator[str]:
    """Dispatch to real or fake streamer."""
    evidence = retrieved_chunks or []
    if OPENAI_API_KEY:
        async for chunk in real_stream(
            message,
            context,
            mode,
            run_id,
            evidence,
            is_evaluation=is_evaluation,
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
        ):
            yield chunk
