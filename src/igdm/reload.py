"""Pick up config.toml changes (new, edited or paused reels) between ticks, without a restart.

A change that doesn't load, or a reel whose link doesn't resolve, leaves the bot running on the
config it already has; the reason goes in kv `config_error` for the dashboard to show.
[email], [llm], `api` and `db_path` changes still need a restart (they are read once at start).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from igdm.config import Config, load_config
from igdm.instagram import InstagramError

log = logging.getLogger(__name__)

CONFIG_ERROR_KEY = "config_error"  # "<ISO UTC> <reason>", "" when the latest config is applied
RETRY_AFTER = timedelta(minutes=5)


class ConfigWatcher:
    """Notices when config.toml changes on disk."""

    def __init__(self, path: Path, cfg: Config, *, applied: bool = True):
        """applied=False: `cfg` may be older than the file (loaded before a slow start), so the
        first check reloads whatever is on disk."""
        self.path = Path(path)
        self.cfg = cfg
        self._applied = self._state() if applied else None
        self._last: tuple[int, int] | None = None
        self._failed: tuple[tuple[int, int], datetime] | None = None

    def _state(self) -> tuple[int, int]:
        st = self.path.stat()
        return (st.st_mtime_ns, st.st_size)

    def pending(self, now: datetime) -> Config | None:
        """A freshly loaded config when the file changed since it was last applied.

        A file state that already failed is tried again only after RETRY_AFTER. A load error
        is raised (and counts as a failed attempt).
        """
        state = self._state()
        if state == self._applied:
            return None
        if self._failed and self._failed[0] == state and now - self._failed[1] < RETRY_AFTER:
            return None
        self._last = state
        self._failed = (state, now)  # cleared by applied()
        return load_config(self.path)

    def applied(self, cfg: Config) -> None:
        self.cfg = cfg
        if self._last is not None:
            self._applied = self._last
        self._failed = None


def _fail(ctx, reason: str) -> bool:
    log.error("config change not applied: %s", reason)
    ctx.db.kv_set(CONFIG_ERROR_KEY, f"{ctx.now().isoformat()} {reason}")
    return False


def apply_config_changes(ctx, watcher: ConfigWatcher) -> bool:
    """Swap in a changed config.toml if it loads and every reel resolves. True if applied."""
    try:
        new = watcher.pending(ctx.now())
    except Exception as e:  # noqa: BLE001 - any load failure keeps the old config
        return _fail(ctx, f"config.toml didn't load ({e})")
    if new is None:
        return False
    new = replace(new, db_path=ctx.config.db_path)  # a dry run keeps its own database
    old = {r.key: r for r in ctx.config.reels}
    media_ids: dict[str, str] = {}
    reels = []
    problems = []
    for r in new.reels:
        prev = old.get(r.key)
        if prev is not None and prev.shortcode == r.shortcode and r.key in ctx.media_ids:
            media_ids[r.key] = ctx.media_ids[r.key]
            reels.append(r)
            continue
        try:
            mid = ctx.ig.find_media_id(r.shortcode)
            problem = None if mid else f'reel "{r.key}": the link didn\'t match any of your posts'
        except InstagramError as e:
            mid, problem = None, f'reel "{r.key}": couldn\'t check the link with Instagram ({e.message})'
        if mid:
            media_ids[r.key] = mid
            reels.append(r)
        elif r.paused and prev is None:
            reels.append(r)  # a paused reel never needs its media id
        else:
            problems.append(problem)
            if prev is not None:  # keep the version that works, but honour a new pause
                reels.append(replace(prev, paused=r.paused))
                if r.key in ctx.media_ids:
                    media_ids[r.key] = ctx.media_ids[r.key]
    ctx.config = replace(new, reels=reels)
    ctx.media_ids = media_ids
    if problems:  # the rest is applied; the failing reels are tried again in RETRY_AFTER
        _fail(ctx, "; ".join(problems))
        return True
    watcher.applied(ctx.config)
    if ctx.db.kv_get(CONFIG_ERROR_KEY):
        ctx.db.kv_set(CONFIG_ERROR_KEY, "")
    log.info("config reloaded (%d reels)", len(new.reels))
    return True
