from __future__ import annotations

import math


def estimate_tokens(text: str) -> int:
    """A deterministic approximation suitable for an MVP hard budget."""
    return max(1, math.ceil(len(text) / 4))


def truncate_to_tokens(text: str, token_budget: int) -> str:
    if token_budget <= 0:
        return ""
    max_chars = token_budget * 4
    if len(text) <= max_chars:
        return text
    marker = "\n# … truncated by Sogi token budget"
    return text[: max(0, max_chars - len(marker))].rstrip() + marker
