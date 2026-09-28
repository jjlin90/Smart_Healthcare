"""Bound optional conversation context before wrapping it in A2A JSON."""

import json

HISTORY_BYTES = 16 * 1024
ENTRY_BYTES = 4 * 1024


def history_size(history: list[dict[str, str]]) -> int:
    return len(json.dumps(history, ensure_ascii=False).encode("utf-8"))


def bounded_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    retained: list[dict[str, str]] = []
    for item in reversed(history[-20:]):
        if item.get("role") not in {"user", "assistant"}:
            continue
        content = str(item.get("content", "")).encode("utf-8")[:ENTRY_BYTES].decode("utf-8", errors="ignore")
        candidate = [{"role": item["role"], "content": content}, *retained]
        if history_size(candidate) > HISTORY_BYTES:
            break
        retained = candidate
    return retained
