"""Atomic JSON replacement for public catalog and alias data only."""
import json
import os
from pathlib import Path
from uuid import uuid4


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
