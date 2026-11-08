"""Add, edit and pause [[reels]] blocks in config.toml, leaving the rest of the file as it was.

Every write goes through write_config: the new text must load with the real load_config before
it replaces config.toml, so a dashboard save can never leave the bot unable to start.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path

import tomlkit

from igdm.config import Config, load_config

REEL_ORDER = ("key", "url", "keyword", "offer", "dm_offer", "material_url", "handle_comments_after",
              "hand_dm_phrases", "ai_notes", "email_subject", "email_body")
MULTILINE = {"email_body"}
CONFIG_LOCK = threading.Lock()  # held around read, edit, write of config.toml


def _empty(v: object) -> bool:
    return v == "" or v == [] or v is None


def _value(name: str, v: object):
    if name in MULTILINE and isinstance(v, str):
        return tomlkit.string(v, multiline=True)
    return v


def add_reel(text: str, fields: dict[str, object]) -> str:
    """Append a new [[reels]] block (keys in REEL_ORDER, empty optional keys left out)."""
    table = tomlkit.table()
    for name in REEL_ORDER:
        if name in fields and not _empty(fields[name]):
            table.add(name, _value(name, fields[name]))
    for name, v in fields.items():  # anything else (e.g. paused) goes after, in given order
        if name not in REEL_ORDER and not _empty(v):
            table.add(name, _value(name, v))
    aot = tomlkit.aot()
    aot.append(table)
    doc = tomlkit.document()
    doc.add("reels", aot)
    return text.rstrip("\n") + "\n\n" + tomlkit.dumps(doc).lstrip("\n")


def _find(doc, key: str):
    for t in doc.get("reels", []):
        if t.get("key") == key:
            return t
    raise KeyError(key)


def _set(table, name: str, value) -> None:
    """Set a key; a new one goes right after the block's last key, before any trailing
    blank lines or comments (which belong visually to the next block)."""
    if name in table:
        table[name] = value
        return
    keys = [k for k, _ in table.value.body if k is not None]
    if keys:
        table.value._insert_after(keys[-1], name, value)
    else:
        table.add(name, value)


def update_reel(text: str, key: str, fields: dict[str, object]) -> str:
    """Set the given keys on one reel's block; "" or [] removes the key. KeyError if no such reel."""
    doc = tomlkit.parse(text)
    table = _find(doc, key)
    for name, v in fields.items():
        if _empty(v):
            if name in table:
                del table[name]
        elif table.get(name) != v:  # unchanged values keep their formatting and comments
            _set(table, name, _value(name, v))
    return tomlkit.dumps(doc)


def set_paused(text: str, key: str, paused: bool) -> str:
    """`paused = true` on that reel, or no paused key at all."""
    doc = tomlkit.parse(text)
    table = _find(doc, key)
    if paused:
        if table.get("paused") is not True:
            _set(table, "paused", True)
    elif "paused" in table:
        del table["paused"]
    else:
        return text
    return tomlkit.dumps(doc)


def write_config(path: Path, text: str) -> Config:
    """Replace config.toml with `text` only if it loads. Returns the loaded config.

    On any error the original file is untouched, no temp file is left behind and the error
    is re-raised.
    """
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config.", suffix=".toml.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        cfg = load_config(Path(tmp))  # same directory, so relative db_path resolves the same
        if path.exists():
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return load_config(path)
