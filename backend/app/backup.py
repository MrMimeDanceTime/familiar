"""Automatic backup of the SQLite DB at app startup.

Runs once in the FastAPI lifespan startup (see main.py). Startup — not
shutdown — is the trigger on purpose: it captures the previous session's
committed state even if the app previously crashed, and a hard crash can't
skip it. Every step is guarded so a backup failure can never stop the app.

The DB copy uses SQLite's online backup API, so it's a consistent snapshot
even if a write is in flight — a plain file copy of a live SQLite DB can tear.

Transport is selected by settings.backup_mode:
  "folder" (default) — write the snapshot into settings.backup_dir. Point that
      at a synced folder (Google Drive / OneDrive / Dropbox / pCloud Drive) and
      that client uploads it off-machine. No API token, no app registration.
  "pcloud" — upload via the pCloud API (settings.pcloud_auth_token). Host is
      region-specific: US -> api.pcloud.com, EU -> eapi.pcloud.com.
  "off" — disabled.
"""

from __future__ import annotations

import logging
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_BACKUP_PREFIX = "familiar-"
_BACKUP_SUFFIX = ".db"


def _snapshot(db_path: Path, dest: Path) -> None:
    """Write a consistent copy of *db_path* to *dest* via SQLite's backup API."""
    src = sqlite3.connect(str(db_path))
    try:
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()


def _pcloud_url(method: str) -> str:
    return f"https://{settings.pcloud_api_host}/{method}"


def _upload(client: httpx.Client, snapshot: Path, filename: str) -> None:
    with snapshot.open("rb") as fh:
        resp = client.post(
            _pcloud_url("uploadfile"),
            params={
                "auth": settings.pcloud_auth_token,
                "folderid": settings.pcloud_folder_id,
                "filename": filename,
                "nopartial": 1,
            },
            files={"file": (filename, fh, "application/octet-stream")},
        )
    resp.raise_for_status()
    body = resp.json()
    # pCloud signals logical errors with a nonzero "result" even on HTTP 200.
    if body.get("result", 0) != 0:
        raise RuntimeError(f"pCloud uploadfile error {body.get('result')}: {body.get('error')}")


def _pcloud_prune(client: httpx.Client, keep: int) -> None:
    """Delete all but the newest *keep* familiar-*.db backups in the folder."""
    resp = client.get(
        _pcloud_url("listfolder"),
        params={"auth": settings.pcloud_auth_token, "folderid": settings.pcloud_folder_id},
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("result", 0) != 0:
        raise RuntimeError(f"pCloud listfolder error {body.get('result')}: {body.get('error')}")

    contents = body.get("metadata", {}).get("contents", []) or []
    backups = [c for c in contents if not c.get("isfolder") and _is_backup_name(c.get("name", ""))]
    # Names embed a sortable UTC timestamp, so lexical sort == chronological.
    backups.sort(key=lambda c: c["name"], reverse=True)

    for stale in backups[keep:]:
        fileid = stale.get("fileid")
        if fileid is None:
            continue
        del_resp = client.get(
            _pcloud_url("deletefile"),
            params={"auth": settings.pcloud_auth_token, "fileid": fileid},
        )
        # Pruning is best-effort — a failed delete shouldn't abort the run.
        if del_resp.is_success and del_resp.json().get("result", 0) != 0:
            logger.warning("pCloud deletefile non-zero result for %s", stale.get("name"))


def _is_backup_name(name: str) -> bool:
    return name.startswith(_BACKUP_PREFIX) and name.endswith(_BACKUP_SUFFIX)


def _backup_via_pcloud(db_path: Path, filename: str) -> None:
    if not settings.pcloud_auth_token:
        logger.warning("backup: mode=pcloud but PCLOUD_AUTH_TOKEN is unset; skipping")
        return
    snapshot = Path(tempfile.gettempdir()) / filename
    try:
        _snapshot(db_path, snapshot)
        with httpx.Client(timeout=60.0) as client:
            _upload(client, snapshot, filename)
            try:
                _pcloud_prune(client, settings.backup_keep)
            except Exception:  # noqa: BLE001 - pruning is non-critical
                logger.warning("backup: pcloud prune failed", exc_info=True)
        logger.info("backup: uploaded %s to pCloud", filename)
    finally:
        snapshot.unlink(missing_ok=True)


def _backup_via_folder(db_path: Path, filename: str) -> None:
    if not settings.backup_dir:
        logger.warning("backup: mode=folder but BACKUP_DIR is unset; skipping")
        return
    dest_dir = Path(settings.backup_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Snapshot to a temp file in the SAME directory, then atomically rename in.
    # Writing straight into a synced folder could let the client upload a
    # half-written file; a rename within the dir is atomic on the same volume.
    tmp = dest_dir / (filename + ".part")
    final = dest_dir / filename
    try:
        _snapshot(db_path, tmp)
        tmp.replace(final)
    finally:
        tmp.unlink(missing_ok=True)

    # Prune: keep the newest N by filename (timestamp sorts chronologically).
    existing = sorted(
        (p for p in dest_dir.glob(f"{_BACKUP_PREFIX}*{_BACKUP_SUFFIX}") if p.is_file()),
        key=lambda p: p.name,
        reverse=True,
    )
    for stale in existing[settings.backup_keep:]:
        try:
            stale.unlink()
        except OSError:  # noqa: PERF203 - best-effort prune
            logger.warning("backup: could not delete old backup %s", stale.name)

    logger.info("backup: wrote %s to %s", filename, dest_dir)


def run_startup_backup() -> None:
    """Snapshot the DB and back it up per settings.backup_mode.

    Best-effort and fully guarded: logs and returns on any failure so app
    startup is never blocked by backup problems.
    """
    mode = settings.backup_mode.lower()
    if mode == "off":
        return

    db_path = settings.db_path
    if not db_path.exists():
        logger.info("backup: no DB at %s yet, skipping", db_path)
        return

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{_BACKUP_PREFIX}{ts}{_BACKUP_SUFFIX}"

    try:
        if mode == "folder":
            _backup_via_folder(db_path, filename)
        elif mode == "pcloud":
            _backup_via_pcloud(db_path, filename)
        else:
            logger.warning("backup: unknown backup_mode=%r; skipping", settings.backup_mode)
    except Exception:  # noqa: BLE001 - never let backup break startup
        logger.warning("backup: failed, continuing without backup", exc_info=True)
