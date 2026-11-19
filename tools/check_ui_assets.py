"""Check the private 4A UI pack without starting NoneBot."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re

from PIL import Image


def check(root):
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        assets = manifest["assets"]
        if not isinstance(assets, dict) or not assets or not isinstance(manifest["spacing"], dict):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        print("UI pack is missing or its manifest is unreadable. Images will use fallback drawing.")
        return 1

    failed = []
    for key, expected in sorted(assets.items()):
        try:
            if not re.fullmatch(r"[a-z0-9_-]+/[a-z0-9_-]+\.png", key):
                raise ValueError
            content = (root / key).read_bytes()
            if hashlib.sha256(content).hexdigest() != expected["sha256"]:
                raise ValueError
            with Image.open(BytesIO(content), formats=["PNG"]) as image:
                if list(image.size) != expected["size"]:
                    raise ValueError
                image.verify()
        except (OSError, ValueError, KeyError, TypeError, SyntaxError, Image.DecompressionBombError):
            failed.append(key)
    for key in failed:
        print(f"Missing or invalid UI asset: {key}")
    print(f"{len(assets) - len(failed)}/{len(assets)} UI images verified.")
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(check(Path(__file__).resolve().parents[1] / "nonebot_plugin_unibot/assets/ui"))
