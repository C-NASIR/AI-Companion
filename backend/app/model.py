"""Model streaming adapters for Session 0 backend."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import AsyncGenerator

from dotenv import find_dotenv, load_dotenv
from openai import AsyncOpenAI

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


async def real_stream(message: str, run_id: str) -> AsyncGenerator[str, None]:
    """Stream completion chunks from OpenAI."""
    client = _get_client()
    stream = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": message}],
        stream=True,
    )
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


async def fake_stream(message: str, run_id: str) -> AsyncGenerator[str, None]:
    """Local deterministic stream when OpenAI credentials are unavailable."""
    snippet = (message.strip() or "…")[:60]
    chunks = [
        f"[fake:{run_id}] ",
        "This is a simulated response. ",
        "User message snippet: ",
        snippet,
        ". ",
        "Replace OPENAI_API_KEY to enable live streaming.",
    ]
    for chunk in chunks:
        await asyncio.sleep(0.15)
        yield chunk


async def stream_chat(message: str, run_id: str) -> AsyncIterator[str]:
    """Dispatch to real or fake streamer."""
    if OPENAI_API_KEY:
        async for chunk in real_stream(message, run_id):
            yield chunk
    else:
        async for chunk in fake_stream(message, run_id):
            yield chunk
