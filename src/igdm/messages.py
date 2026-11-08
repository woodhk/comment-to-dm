from __future__ import annotations

import random
import re
import unicodedata

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_EMOJI_SUFFIXES = ["🙌", "👍", "📩", "🤝"]


def first_name(display_name: str | None) -> str | None:
    if not display_name:
        return None
    # skip leading tokens with no letters, e.g. the emoji in "✨ Sam"
    return next((p for p in display_name.split() if any(ch.isalpha() for ch in p)), None)


def render(template: str, *, name: str, keyword: str, material_url: str = "") -> str:
    """Emails and follow-ups: {name}, {keyword} and {material_url} only."""
    return template.format(name=name, keyword=keyword, material_url=material_url)


def render_dm1(template: str, *, name: str, keyword: str, offer: str) -> str:
    """The first DM (dm1 / repeat_dm1): {name}, {keyword} and the reel's {offer}."""
    return template.format(name=name, keyword=keyword, offer=offer)


def _has_emoji(text: str) -> bool:
    return any(unicodedata.category(c) == "So" for c in text)


def pick_public_reply(pool: list[str], recent: list[str], rng: random.Random) -> str:
    recent_set = set(recent)
    candidates = [line for line in pool if line not in recent_set] or pool
    text = rng.choice(candidates)
    if rng.random() < 0.5:
        text = text[:1].upper() + text[1:]
    if not _has_emoji(text) and rng.random() < 0.25:
        text += " " + rng.choice(_EMOJI_SUFFIXES)
    return text


def base_of(text: str, pool: list[str]) -> str:
    low = text.lower()
    matches = [line for line in pool if low.startswith(line.lower())]
    if not matches:
        raise ValueError(f"text not derived from pool: {text!r}")
    return max(matches, key=len)


def pick_got_it(pool: list[str], rng: random.Random) -> str:
    return rng.choice(pool)


def find_email(text: str) -> str | None:
    m = _EMAIL_RE.search(text)
    if not m:
        return None
    return m.group(0).lower().rstrip(".,!")


COMMON_DOMAINS = ("gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com",
                  "live.com", "protonmail.com", "googlemail.com", "proton.me")


def _osa(a: str, b: str) -> int:
    """Edit distance counting a swap of two neighbouring letters as one edit."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def suspect_domain(email: str) -> str | None:
    """The common domain a mistyped address probably meant (gmial.com -> gmail.com), else None."""
    domain = email.rpartition("@")[2].lower()
    if domain in COMMON_DOMAINS:
        return None
    near = [d for d in COMMON_DOMAINS if _osa(domain, d) == 1]
    return near[0] if near else None
