"""Repository acquisition: clone / download / extract / collect."""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path
from urllib.parse import quote

import httpx

from ....settings import settings
from ....utils.http_client import get_http_client
from ....utils.paths import CACHE_DIR
from ..github import _GITHUB_API_URL, _headers, _retry_after, _token
from ..tree_sitter_evidence import language_for_path
from .models import (
    _SKIP_DIRS,
    _SKIP_SUFFIXES,
    _SPARSE_SKIP_PATTERNS,
    LOGGER,
    MAX_ARCHIVE_BYTES,
    MAX_EXTRACTED_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    SnapshotError,
)


def _safe_rmtree(path: Path | str) -> None:
    """Recursively remove a directory, clearing read-only flags on Windows if needed."""
    target = Path(path)
    if not target.exists():
        return
    with contextlib.suppress(Exception):
        for p in target.rglob("*"):
            with contextlib.suppress(Exception):
                os.chmod(p, stat.S_IWRITE)
    shutil.rmtree(target, ignore_errors=True)


def _clone_sparse_repo(
    repository: str, branch: str, sha: str, ref: str | None = None
) -> Path | None:
    """Fast partial clone: download only commit/tree and <=1MB code/text blobs.

    Uses Git partial clone with sparse-checkout to avoid downloading large media,
    binary archives, model checkpoints, and documents over the network.
    """
    git_bin = shutil.which("git")
    if not git_bin:
        return None
    token = _token()
    owner, repo = repository.split("/", 1)
    if token:
        remote_url = f"https://x-access-token:{token}@github.com/{quote(owner)}/{quote(repo)}.git"
    else:
        remote_url = f"https://github.com/{quote(owner)}/{quote(repo)}.git"

    dest = Path(CACHE_DIR) / "code_fetch_extracts" / f"{owner}__{repo}__{sha[:12]}"
    _safe_rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    target_ref = ref or branch or "HEAD"
    is_sha = len(target_ref) == 40 and all(c in "0123456789abcdefABCDEF" for c in target_ref)

    try:
        clone_cmd = [
            git_bin,
            "-c",
            "core.quotepath=false",
            "clone",
            "--depth",
            "1",
            "--filter=blob:limit=1m",
            "--sparse",
        ]
        if is_sha:
            clone_cmd.extend([remote_url, str(dest)])
            res = subprocess.run(clone_cmd, capture_output=True, timeout=60)
            if res.returncode != 0:
                _safe_rmtree(dest)
                dest.mkdir(parents=True, exist_ok=True)
                subprocess.run([git_bin, "init", str(dest)], capture_output=True, check=True)
                subprocess.run(
                    [git_bin, "-C", str(dest), "remote", "add", "origin", remote_url],
                    capture_output=True,
                    check=True,
                )
                subprocess.run(
                    [git_bin, "-C", str(dest), "config", "core.quotepath", "false"],
                    capture_output=True,
                    check=True,
                )
                fetch_res = subprocess.run(
                    [
                        git_bin,
                        "-C",
                        str(dest),
                        "fetch",
                        "--depth",
                        "1",
                        "--filter=blob:limit=1m",
                        "origin",
                        target_ref,
                    ],
                    capture_output=True,
                    timeout=60,
                )
                if fetch_res.returncode != 0:
                    _safe_rmtree(dest)
                    return None
        else:
            clone_cmd.extend(["--branch", target_ref, remote_url, str(dest)])
            res = subprocess.run(clone_cmd, capture_output=True, timeout=60)
            if res.returncode != 0:
                _safe_rmtree(dest)
                dest.mkdir(parents=True, exist_ok=True)
                clone_cmd2 = [
                    git_bin,
                    "-c",
                    "core.quotepath=false",
                    "clone",
                    "--depth",
                    "1",
                    "--filter=blob:limit=1m",
                    "--sparse",
                    remote_url,
                    str(dest),
                ]
                res2 = subprocess.run(clone_cmd2, capture_output=True, timeout=60)
                if res2.returncode != 0:
                    _safe_rmtree(dest)
                    return None

        subprocess.run(
            [
                git_bin,
                "-C",
                str(dest),
                "sparse-checkout",
                "set",
                "--no-cone",
                *_SPARSE_SKIP_PATTERNS,
            ],
            capture_output=True,
            timeout=30,
            check=True,
        )
        checkout_cmd = [git_bin, "-C", str(dest), "checkout"]
        if is_sha:
            checkout_cmd.append(target_ref)
        co_res = subprocess.run(checkout_cmd, capture_output=True, timeout=30)
        if co_res.returncode != 0:
            _safe_rmtree(dest)
            return None
        return dest
    except Exception as exc:
        LOGGER.warning(
            "Git sparse clone failed for %s: %s; falling back to tarball", repository, exc
        )
        _safe_rmtree(dest)
        return None


async def _resolve_main_commit(repository: str, *, ref: str | None = None) -> tuple[str, str]:
    token = _token()
    client = await get_http_client()
    owner, repo = repository.split("/", 1)
    repo_url = f"{_GITHUB_API_URL}/repos/{quote(owner)}/{quote(repo)}"
    if ref:
        try:
            commit_response = await client.get(
                f"{repo_url}/commits/{quote(ref)}",
                headers=_headers(token),
                timeout=settings.search_retrieve_budget_seconds,
            )
        except httpx.HTTPError as exc:
            raise SnapshotError(
                f"GitHub commit lookup for ref '{ref}' failed: {str(exc) or type(exc).__name__}"
            ) from exc
        if commit_response.status_code == 200:
            commit_payload = commit_response.json() if commit_response.content else {}
            sha = commit_payload.get("sha") if isinstance(commit_payload, dict) else None
            if isinstance(sha, str) and len(sha) >= 7:
                return ref, sha
        raise SnapshotError(
            f"GitHub commit lookup for ref '{ref}' returned HTTP {commit_response.status_code}",
            retry_after_seconds=_retry_after(commit_response),
        )
    try:
        response = await client.get(
            repo_url, headers=_headers(token), timeout=settings.search_retrieve_budget_seconds
        )
    except httpx.HTTPError as exc:
        raise SnapshotError(
            f"GitHub repository lookup failed: {str(exc) or type(exc).__name__}"
        ) from exc
    if response.status_code != 200:
        raise SnapshotError(
            f"GitHub repository lookup returned HTTP {response.status_code}",
            retry_after_seconds=_retry_after(response),
        )
    payload = response.json() if response.content else {}
    default_branch = payload.get("default_branch") if isinstance(payload, dict) else None
    branch = default_branch if isinstance(default_branch, str) and default_branch else "main"
    try:
        commit_response = await client.get(
            f"{repo_url}/commits/{quote(branch)}",
            headers=_headers(token),
            timeout=settings.search_retrieve_budget_seconds,
        )
    except httpx.HTTPError as exc:
        raise SnapshotError(
            f"GitHub commit lookup failed: {str(exc) or type(exc).__name__}"
        ) from exc
    if commit_response.status_code == 404 and branch != "main":
        branch = "main"
        commit_response = await client.get(
            f"{repo_url}/commits/{quote(branch)}",
            headers=_headers(token),
            timeout=settings.search_retrieve_budget_seconds,
        )
    if commit_response.status_code != 200:
        raise SnapshotError(
            f"GitHub commit lookup returned HTTP {commit_response.status_code}",
            retry_after_seconds=_retry_after(commit_response),
        )
    commit_payload = commit_response.json() if commit_response.content else {}
    sha = commit_payload.get("sha") if isinstance(commit_payload, dict) else None
    if not isinstance(sha, str) or len(sha) < 7:
        raise SnapshotError("GitHub commit lookup omitted sha")
    return branch, sha


async def _download_tarball(repository: str, sha: str) -> Path:
    token = _token()
    client = await get_http_client()
    owner, repo = repository.split("/", 1)
    url = f"{_GITHUB_API_URL}/repos/{quote(owner)}/{quote(repo)}/tarball/{quote(sha)}"
    dest = Path(CACHE_DIR) / "code_fetch_extracts" / f"{owner}__{repo}__{sha[:12]}"
    _safe_rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        async with client.stream(
            "GET",
            url,
            headers=_headers(token),
            timeout=settings.search_retrieve_budget_seconds,
            follow_redirects=True,
        ) as response:
            if response.status_code != 200:
                raise SnapshotError(
                    f"GitHub tarball returned HTTP {response.status_code}",
                    retry_after_seconds=_retry_after(response),
                )
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > MAX_ARCHIVE_BYTES:
                raise SnapshotError("repository archive exceeds the code_fetch size budget")
            chunks = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise SnapshotError("repository archive exceeds the code_fetch size budget")
                chunks.append(chunk)
            data = b"".join(chunks)
    except httpx.HTTPError as exc:
        raise SnapshotError(
            f"GitHub tarball download failed: {str(exc) or type(exc).__name__}"
        ) from exc
    await asyncio.to_thread(_extract_tarball, data, dest)
    return dest


def _extract_tarball(data: bytes, dest: Path) -> None:
    extracted = 0
    files = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive:
            name = member.name.replace("\\", "/")
            parts = [part for part in name.split("/") if part not in ("", ".")]
            if not parts or any(part == ".." for part in parts):
                continue
            relative = Path(*parts[1:]) if len(parts) > 1 else Path()
            if not str(relative) or any(part in _SKIP_DIRS for part in relative.parts):
                continue
            if relative.suffix.casefold() in _SKIP_SUFFIXES:
                continue
            target = dest / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            if int(member.size or 0) > MAX_FILE_BYTES:
                continue
            files += 1
            if files > MAX_FILES:
                break
            extracted += int(member.size or 0)
            if extracted > MAX_EXTRACTED_BYTES:
                break
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            with source, target.open("wb") as handle:
                handle.write(source.read(MAX_FILE_BYTES + 1))


def _collect_files(
    source_root: Path,
    dest: Path,
) -> tuple[int, bool, int, list[tuple[str, str | None, int, str]]]:
    records: list[tuple[str, str | None, int, str]] = []
    truncated = False
    copied = 0
    skipped_binary = 0
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_root)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix.casefold() in _SKIP_SUFFIXES:
            continue
        if copied >= MAX_FILES:
            truncated = True
            break
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            truncated = True
            continue
        if b"\0" in data[:1024]:
            skipped_binary += 1
            continue
        text = data.decode("utf-8", errors="replace")
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        records.append(
            (relative.as_posix(), language_for_path(relative.as_posix()), len(data), text)
        )
        copied += 1
    return copied, truncated, skipped_binary, records
