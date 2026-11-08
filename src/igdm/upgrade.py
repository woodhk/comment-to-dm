"""Bring an older config.toml up to the settings version this bot needs.

Each step edits the document in place (tomlkit keeps comments and layout) and is keyed by the
version it upgrades *to*. Add a step here, and a frozen old example under tests/data/old_configs,
whenever CONFIG_VERSION goes up.
"""
from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import tomlkit

from igdm.config import CONFIG_VERSION, DEFAULT_STYLE, settings_version
from igdm.reelfile import CONFIG_LOCK, write_config


def _last_plain_key(doc: tomlkit.TOMLDocument) -> str | None:
    """The last key before the first table, i.e. where new top-level keys can go."""
    last = None
    for key, value in doc.body:
        if key is None:
            continue
        if isinstance(value, (tomlkit.items.Table, tomlkit.items.AoT)):
            break
        last = key.key
    return last


def _to_v2(doc: tomlkit.TOMLDocument) -> None:
    from_name = str(doc.get("email", {}).get("from_name", "")).split()
    owner = tomlkit.table()
    owner.add("name", from_name[0] if from_name else "me")
    owner.add("about", "")
    owner.add("style", DEFAULT_STYLE)
    after = _last_plain_key(doc)
    if after is None:
        doc.add("config_version", 2)
        doc.add(tomlkit.nl())
        doc.add("owner", owner)
        return
    # tomlkit has no public "insert here": a plain doc.add would put the key at the very end,
    # inside the last table. The owner table goes right below the top-level keys.
    doc._insert_after(after, "config_version", 2)
    owner.trivia.indent = "\n"  # a blank line before [owner]
    doc._insert_after("config_version", "owner", owner)


STEPS: dict[int, Callable[[tomlkit.TOMLDocument], None]] = {2: _to_v2}


def upgrade_text(text: str) -> tuple[str, int, int]:
    """Return (new_text, from_version, to_version)."""
    doc = tomlkit.parse(text)
    start = settings_version(doc)
    if start > CONFIG_VERSION:
        raise ValueError(
            f"config.toml uses settings version {start}, newer than this bot understands "
            f"({CONFIG_VERSION}). Update the bot's code first."
        )
    if start == CONFIG_VERSION:
        return text, start, start
    for version in range(start + 1, CONFIG_VERSION + 1):
        STEPS[version](doc)
    return tomlkit.dumps(doc), start, CONFIG_VERSION


def upgrade_file(path: Path, now: datetime) -> tuple[int, int, Path | None]:
    """Upgrade config.toml in place. Returns (from, to, backup); backup is None if nothing changed."""
    path = Path(path)
    with CONFIG_LOCK:
        text = path.read_text(encoding="utf-8")
        new_text, start, end = upgrade_text(text)
        if start == end:
            return start, end, None
        backup = path.with_name(f"{path.name}.bak-{now:%Y%m%d-%H%M%S}")
        shutil.copy2(path, backup)
        write_config(path, new_text)
    return start, end, backup
