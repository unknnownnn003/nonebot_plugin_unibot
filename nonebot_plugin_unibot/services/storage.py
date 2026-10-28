"""Per-player commits shared by native commands, imports and queued sync.

Network and parsing happen outside the lock. Merge uses the latest file while
holding a thread lock; replacements are atomic per file, not across both files.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import threading
from functools import wraps
from uuid import uuid4

_locks = {}
_factory = threading.Lock()


def user_lock(user_id):
    if not str(user_id).isdigit() or len(str(user_id)) > 20:
        raise ValueError("Invalid player identity")
    with _factory:
        return _locks.setdefault(str(user_id), threading.RLock())


def locked_player(function):
    @wraps(function)
    def locked(user_id, *args, **kwargs):
        with user_lock(user_id):
            return function(user_id, *args, **kwargs)
    return locked


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=4)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class PartialWriteError(OSError):
    """Scores were replaced but the independent profile replacement failed."""


def commit_scores(user_id, rows, player_info=None, *, manual=False):
    from .. import get_player_score as s
    with user_lock(user_id):
        path = s._manual_score_path(str(user_id)) if manual else str(Path(s.SCORE_DIR) / f"{user_id}.json")
        existing = s._read_score_json(path)
        merged = s._replace_manual_score(existing, rows[0]) if manual else s._merge_score_items(existing, rows)
        merged = s._inject_level_value(merged, s._load_song_meta())
        # Retain the Rin profile-only case without creating an empty score file.
        if rows:
            atomic_json(path, merged)
        if player_info:
            try:
                atomic_json(Path(s.SCORE_DIR) / f"{user_id}_info.json", player_info)
            except OSError as exc:
                if rows:
                    raise PartialWriteError("Score commit complete; profile commit failed") from exc
                raise
        return len(merged)
