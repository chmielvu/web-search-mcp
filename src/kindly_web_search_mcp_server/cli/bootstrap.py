from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


_package_dir = Path(__file__).resolve().parent
_repo_root = _package_dir.parents[2]
load_dotenv(_repo_root / ".env")
load_dotenv()
