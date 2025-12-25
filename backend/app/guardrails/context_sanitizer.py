"""Retrieval context sanitization rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..events import context_sanitized_event
from .base import EventPublisher


CODE_BLOCK_PATTERN = re.compile(r"```[\s\S]*?```", re.MULTILINE)
DIRECTIVE_PATTERN = re.compile(
    r"^\s*(ignore|disregard|forget|you must|execute)\b", re.IGNORECASE
)


@dataclass(slots=True)
class SanitizationResult:
    """Describes the sanitization applied to a chunk."""

    sanitized_text: str
    sanitized: bool
    notes: str | None


class ContextSanitizer:
    """Removes executable language from retrieved chunks."""

    def __init__(self, publisher: EventPublisher):
        self.publisher = publisher

    async def sanitize_chunk(self, run_id: str, chunk_id: str, text: str) -> str:
        """Return sanitized text and emit events describing the operation."""
        result = self._sanitize(text)
        await self.publisher.publish(
            context_sanitized_event(
                run_id,
                original_chunk_id=chunk_id,
                sanitization_applied=result.sanitized,
                notes=result.notes,
            )
        )
        return result.sanitized_text

    def _sanitize(self, text: str) -> SanitizationResult:
        working = text or ""
        notes: list[str] = []
        working, code_blocks_removed = self._strip_code_blocks(working)
        if code_blocks_removed:
            notes.append(f"removed {code_blocks_removed} code block(s)")
        working, directives_removed = self._strip_directives(working)
        if directives_removed:
            notes.append(f"removed {directives_removed} directive sentence(s)")
        collapsed = self._collapse_whitespace(working)
        sanitized = collapsed != (text or "")
        return SanitizationResult(
            sanitized_text=collapsed,
            sanitized=sanitized,
            notes="; ".join(notes) if notes else None,
        )

    @staticmethod
    def _strip_code_blocks(text: str) -> tuple[str, int]:
        matches = CODE_BLOCK_PATTERN.findall(text)
        sanitized = CODE_BLOCK_PATTERN.sub("", text)
        return sanitized, len(matches)

    @staticmethod
    def _strip_directives(text: str) -> tuple[str, int]:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        kept: list[str] = []
        removed = 0
        for sentence in sentences:
            stripped = sentence.strip()
            if not stripped:
                continue
            if DIRECTIVE_PATTERN.match(stripped):
                removed += 1
                continue
            kept.append(stripped)
        sanitized = " ".join(kept)
        return sanitized, removed

    @staticmethod
    def _collapse_whitespace(text: str) -> str:
        tokens = text.split()
        if not tokens:
            return ""
        return " ".join(tokens)
