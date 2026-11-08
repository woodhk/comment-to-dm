"""The owner's voice rules for anything an AI writes in their name."""

from __future__ import annotations

import re
from typing import Iterable

_DASHES = ("—", "–")  # em dash, en dash
# "Quick update: ..." at the start of the text or of a sentence (not "10:30" or "https://")
_LABEL = re.compile(r"(?:^|[.!?]\s+|\n)\s*[A-Za-z']+(?:\s[A-Za-z']+)?:\s")
_SENTENCE_SPLIT = re.compile(r"[.!?]+(?:\s+|$)|\n+")


def problems(text: str, deny: Iterable[str]) -> list[str]:
    """What breaks the rules in `text` (empty when it's fine)."""
    found = []
    if any(d in text for d in _DASHES):
        found.append("uses a dash (em dash or en dash)")
    if _LABEL.search(text):
        found.append('uses a "label: explanation" opener')
    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    if len(sentences) >= 2 and all(len(s.split()) < 4 for s in sentences):
        found.append("reads as clipped fragment sentences")
    for word in deny:
        if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text, re.IGNORECASE):
            found.append(f"uses a deny-listed word: {word!r}")
    return found
