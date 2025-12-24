"""Tool intent parsing for the intelligence planner."""

from __future__ import annotations

import re
from typing import Sequence

from ..mcp.schema import ToolDescriptor

_SYMBOL_EXPR = re.compile(r"(-?\d+(?:\.\d+)?)\s*([+\-*/])\s*(-?\d+(?:\.\d+)?)")
_KEYWORD_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(
            r"\badd\s+(-?\d+(?:\.\d+)?)\s+(?:and|to)\s+(-?\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        "add",
        "normal",
    ),
    (
        re.compile(
            r"\bsubtract\s+(-?\d+(?:\.\d+)?)\s+from\s+(-?\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        "subtract",
        "reverse",
    ),
    (
        re.compile(
            r"\b(?:multiply|times)\s+(-?\d+(?:\.\d+)?)\s+(?:and|by)\s+(-?\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        "multiply",
        "normal",
    ),
    (
        re.compile(
            r"\bdivide\s+(-?\d+(?:\.\d+)?)\s+by\s+(-?\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        "divide",
        "normal",
    ),
]

_REPO_KEYWORD_PATTERN = re.compile(
    r"(?:repo|repository)\s+(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_REPO_URL_PATTERN = re.compile(
    r"github\.com/(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_REPO_LOOSE_PATTERN = re.compile(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b")
_PATH_HINT_PATTERN = re.compile(
    r"(?:path|directory|folder)\s+(?P<path>[A-Za-z0-9_.\-/]+)",
    re.IGNORECASE,
)
_FILE_HINT_PATTERN = re.compile(
    r"file\s+(?:at\s+|from\s+)?(?P<path>[A-Za-z0-9_.\-/]+)",
    re.IGNORECASE,
)


def _parse_number(token: str) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


def _match_symbol_expression(message: str) -> dict[str, float] | None:
    match = _SYMBOL_EXPR.search(message)
    if not match:
        return None
    a = _parse_number(match.group(1))
    b = _parse_number(match.group(3))
    op = match.group(2)
    if a is None or b is None:
        return None
    mapping = {"+": "add", "-": "subtract", "*": "multiply", "/": "divide"}
    operation = mapping.get(op)
    if not operation:
        return None
    return {"operation": operation, "a": a, "b": b}


def _match_keyword_expression(message: str) -> dict[str, float] | None:
    for pattern, operation, order in _KEYWORD_PATTERNS:
        match = pattern.search(message)
        if not match:
            continue
        first = match.group(1)
        second = match.group(2)
        a = _parse_number(first)
        b = _parse_number(second)
        if a is None or b is None:
            continue
        if order == "reverse":
            a, b = b, a
        return {"operation": operation, "a": a, "b": b}
    return None


def _detect_calculator_request(message: str) -> dict[str, float] | None:
    symbol_match = _match_symbol_expression(message)
    if symbol_match:
        return symbol_match
    return _match_keyword_expression(message)


def _extract_repo_identifier(message: str) -> str | None:
    url_match = _REPO_URL_PATTERN.search(message)
    if url_match:
        return url_match.group("repo")
    keyword_match = _REPO_KEYWORD_PATTERN.search(message)
    if keyword_match:
        return keyword_match.group("repo")
    lowered = message.lower()
    if "repo" in lowered or "repository" in lowered or "github" in lowered:
        loose_match = _REPO_LOOSE_PATTERN.search(message)
        if loose_match:
            return loose_match.group(1)
    return None


def _extract_path_hint(message: str) -> str | None:
    match = _PATH_HINT_PATTERN.search(message)
    if match:
        return match.group("path").strip().strip("\"'")
    return None


def _extract_file_path(message: str) -> str | None:
    match = _FILE_HINT_PATTERN.search(message)
    if not match:
        return None
    return match.group("path").strip().strip("\"'")


def _detect_github_list_files(message: str) -> dict[str, str] | None:
    lowered = message.lower()
    if not any(keyword in lowered for keyword in ("list", "show", "what are")):
        return None
    if not any(keyword in lowered for keyword in ("file", "files", "folder", "directory")):
        return None
    repo = _extract_repo_identifier(message)
    if not repo:
        return None
    payload: dict[str, str] = {"repo": repo}
    path = _extract_path_hint(message)
    if path:
        payload["path"] = path
    return payload


def _detect_github_read_file(message: str) -> dict[str, str] | None:
    lowered = message.lower()
    if not any(keyword in lowered for keyword in ("read", "open", "show", "view")):
        return None
    if "file" not in lowered:
        return None
    repo = _extract_repo_identifier(message)
    if not repo:
        return None
    path = _extract_file_path(message) or _extract_path_hint(message)
    if not path:
        return None
    return {"repo": repo, "path": path}


def match_tool_intent(
    message: str, allowed_tools: Sequence[ToolDescriptor]
) -> tuple[ToolDescriptor, dict[str, object]] | None:
    for descriptor in allowed_tools:
        if descriptor.name == "calculator":
            args = _detect_calculator_request(message)
        elif descriptor.name == "github.list_files":
            args = _detect_github_list_files(message)
        elif descriptor.name == "github.read_file":
            args = _detect_github_read_file(message)
        else:
            args = None
        if args:
            return descriptor, args
    return None
