from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _atomic_write(path: str | Path, content: str) -> str:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return str(target)


def write_text_atomic(path: str | Path, content: str) -> str:
    return _atomic_write(path, content)


def write_json_atomic(path: str | Path, payload: Any) -> str:
    return _atomic_write(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
    )
