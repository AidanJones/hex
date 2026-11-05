"""Tiny .env loader (stdlib). Real environment variables always win."""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(*paths: Path) -> list[Path]:
    loaded = []
    for p in paths:
        if not p.is_file():
            continue
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.removeprefix("export ").partition("=")
            key, val = key.strip(), val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                val = val[1:-1]
            else:
                val = val.split(" #", 1)[0].strip()   # trailing comment
            os.environ.setdefault(key, val)
        loaded.append(p)
    return loaded
