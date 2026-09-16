"""Artifact storage: front-mattered clean .md files, per-domain boilerplate DB,
manifest (incremental scraping), run report. No chunking — downstream's job."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def estimate_tokens(text: str) -> int:
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return max(1, round(len(text) / 3.8))  # documented approximation


def write_page(pages_dir: Path, rel: str, fm: dict, body: str) -> Path:
    """Write front matter + body atomically (tmp file + os.replace)."""
    p = pages_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    header = "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True,
                                      default_flow_style=False).strip() + "\n---\n\n"
    tmp = p.with_suffix(p.suffix + f".tmp{os.getpid()}")
    tmp.write_text(header + body, encoding="utf-8")
    os.replace(tmp, p)
    return p


class BoilerplateDB:
    """Kohlschütter-style template detection via cross-page block repetition.

    Contract with ``cleaner._is_site_boilerplate``: ``lookup()`` returns
    ``{block_hash: distinct_page_count}`` plus a ``__pages_seen`` metadata key.
    Each hash is counted AT MOST ONCE per page (per-page idempotency fixes the
    prototype's double-counting bug in two-pass mode and across re-runs).
    """

    def __init__(self, path: Path, max_blocks_per_domain: int = 20000):
        self.path = path
        self.max_blocks = max_blocks_per_domain
        self.domains: Dict[str, dict] = {}
        try:
            if path.exists():
                self.domains = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self.domains = {}

    def _entry(self, domain: str) -> dict:
        return self.domains.setdefault(domain, {"pages_seen": 0, "blocks": {}, "pages": {}})

    def observe(self, domain: str, page_key: str, block_hashes: List[str]) -> None:
        """Record blocks for one page. Idempotent per (domain, page_key)."""
        e = self._entry(domain)
        if page_key in e["pages"]:
            return
        e["pages"][page_key] = True
        e["pages_seen"] = len(e["pages"])
        blocks = e["blocks"]
        for h in set(block_hashes):          # once per page, not per occurrence
            blocks[h] = blocks.get(h, 0) + 1
        # bound the pages set (keep counts, drop old page keys beyond 5000)
        if len(e["pages"]) > 5000:
            e["pages"] = dict(list(e["pages"].items())[-2500:])

    def lookup(self, domain: str) -> Dict[str, int]:
        e = self.domains.get(domain) or {}
        out = {h: v for h, v in (e.get("blocks") or {}).items()}
        out["__pages_seen"] = e.get("pages_seen", 0)
        return out

    def save(self) -> None:
        for d, e in self.domains.items():
            if len(e.get("blocks", {})) > self.max_blocks:
                top = sorted(e["blocks"].items(), key=lambda kv: -kv[1])[: self.max_blocks]
                e["blocks"] = dict(top)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.domains, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)


class Manifest:
    def __init__(self, records: Optional[Dict] = None, stats: Optional[Dict] = None):
        self.records: Dict[str, dict] = records or {}
        self.stats: Dict = stats or {}

    @classmethod
    def load(cls, out_dir: Path) -> "Manifest":
        p = out_dir / "manifest.json"
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                return cls(d.get("records"), d.get("stats"))
            except Exception:
                pass
        return cls()

    def save(self, out_dir: Path) -> None:
        self.stats["updated_at"] = utcnow_iso()
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / "manifest.json"
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"records": self.records, "stats": self.stats},
                                  indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    def unchanged(self, url: str, content_hash: str) -> bool:
        rec = self.records.get(url)
        return bool(rec and rec.get("content_hash") == content_hash
                    and rec.get("status") in ("published", "unchanged", "quarantined", "review"))


def write_report(out_dir: Path, report: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "report-latest.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    os.replace(tmp, p)
    return p
