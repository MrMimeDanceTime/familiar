import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import backup
from app.config import settings


def _make_db(path: Path) -> None:
    c = sqlite3.connect(path)
    c.execute("create table t(x)")
    c.execute("insert into t values (7)")
    c.commit()
    c.close()


def test_snapshot_produces_valid_copy(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    dst = tmp_path / "snap.db"

    backup._snapshot(src, dst)

    out = sqlite3.connect(dst)
    assert out.execute("select x from t").fetchone()[0] == 7
    out.close()


def test_run_startup_backup_off_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "backup_mode", "off")
    with patch("app.backup.httpx.Client") as client:
        backup.run_startup_backup()
        client.assert_not_called()


def test_run_startup_backup_failure_is_swallowed(monkeypatch, tmp_path):
    # A real DB so snapshot succeeds, then the transport blows up — startup must
    # still proceed (no exception propagates). db_path is a read-only property
    # derived from familiar_db_path, so set that (absolute -> used as-is).
    db = tmp_path / "familiar.db"
    _make_db(db)
    monkeypatch.setattr(settings, "backup_mode", "pcloud")
    monkeypatch.setattr(settings, "pcloud_auth_token", "tok")
    monkeypatch.setattr(settings, "familiar_db_path", str(db))

    with patch("app.backup.httpx.Client", side_effect=RuntimeError("network down")):
        backup.run_startup_backup()  # should not raise


# ── folder mode ────────────────────────────────────────────────────────────

def test_folder_mode_writes_valid_snapshot(monkeypatch, tmp_path):
    db = tmp_path / "familiar.db"
    _make_db(db)
    dest = tmp_path / "synced"
    monkeypatch.setattr(settings, "backup_mode", "folder")
    monkeypatch.setattr(settings, "backup_dir", str(dest))
    monkeypatch.setattr(settings, "familiar_db_path", str(db))

    backup.run_startup_backup()

    written = list(dest.glob("familiar-*.db"))
    assert len(written) == 1
    # No leftover .part temp file.
    assert not list(dest.glob("*.part"))
    # The written file is a real, queryable DB copy.
    out = sqlite3.connect(written[0])
    assert out.execute("select x from t").fetchone()[0] == 7
    out.close()


def test_folder_mode_prunes_to_keep(monkeypatch, tmp_path):
    db = tmp_path / "familiar.db"
    _make_db(db)
    dest = tmp_path / "synced"
    dest.mkdir()
    # Seed 3 old backups (sortable timestamps) + an unrelated file.
    for ts in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
        (dest / f"familiar-{ts}.db").write_bytes(b"old")
    (dest / "notes.txt").write_bytes(b"keep me")

    monkeypatch.setattr(settings, "backup_mode", "folder")
    monkeypatch.setattr(settings, "backup_dir", str(dest))
    monkeypatch.setattr(settings, "backup_keep", 2)
    monkeypatch.setattr(settings, "familiar_db_path", str(db))

    backup.run_startup_backup()  # writes a new (newest) snapshot

    remaining = sorted(p.name for p in dest.glob("familiar-*.db"))
    # keep=2: the new snapshot + the newest prior one; oldest two pruned.
    assert len(remaining) == 2
    assert "familiar-20260101T000000Z.db" not in remaining  # oldest pruned
    assert (dest / "notes.txt").exists()  # unrelated file untouched


def test_folder_mode_noop_without_dir(monkeypatch, tmp_path):
    db = tmp_path / "familiar.db"
    _make_db(db)
    monkeypatch.setattr(settings, "backup_mode", "folder")
    monkeypatch.setattr(settings, "backup_dir", "")
    monkeypatch.setattr(settings, "familiar_db_path", str(db))
    # Should log-and-skip, not raise.
    backup.run_startup_backup()


# ── pcloud mode ────────────────────────────────────────────────────────────

def _fake_client(deleted, uploaded):
    def fake_get(url, params=None):
        r = MagicMock()
        r.is_success = True
        r.raise_for_status = lambda: None
        if url.endswith("listfolder"):
            r.json = lambda: {"result": 0, "metadata": {"contents": [
                {"name": "familiar-20260101T000000Z.db", "isfolder": False, "fileid": 1},
                {"name": "familiar-20260102T000000Z.db", "isfolder": False, "fileid": 2},
                {"name": "familiar-20260103T000000Z.db", "isfolder": False, "fileid": 3},
                {"name": "notes.txt", "isfolder": False, "fileid": 99},
            ]}}
        elif url.endswith("deletefile"):
            deleted.append(params["fileid"])
            r.json = lambda: {"result": 0}
        return r

    def fake_post(url, params=None, files=None):
        uploaded.append(params["filename"])
        r = MagicMock()
        r.raise_for_status = lambda: None
        r.json = lambda: {"result": 0, "fileids": [123]}
        return r

    client = MagicMock()
    client.get = fake_get
    client.post = fake_post
    client.__enter__ = lambda s: client
    client.__exit__ = lambda *a: False
    return client


def test_pcloud_mode_uploads_and_prunes(monkeypatch, tmp_path):
    db = tmp_path / "familiar.db"
    _make_db(db)
    monkeypatch.setattr(settings, "backup_mode", "pcloud")
    monkeypatch.setattr(settings, "pcloud_auth_token", "tok")
    monkeypatch.setattr(settings, "pcloud_folder_id", 0)
    monkeypatch.setattr(settings, "backup_keep", 1)
    monkeypatch.setattr(settings, "familiar_db_path", str(db))

    deleted, uploaded = [], []
    with patch("app.backup.httpx.Client", return_value=_fake_client(deleted, uploaded)):
        backup.run_startup_backup()

    assert len(uploaded) == 1 and uploaded[0].startswith("familiar-")
    # keep=1 -> newest (fileid 3) kept, older two (1, 2) pruned; .txt ignored.
    assert sorted(deleted) == [1, 2]


def test_pcloud_mode_noop_without_token(monkeypatch, tmp_path):
    db = tmp_path / "familiar.db"
    _make_db(db)
    monkeypatch.setattr(settings, "backup_mode", "pcloud")
    monkeypatch.setattr(settings, "pcloud_auth_token", "")
    monkeypatch.setattr(settings, "familiar_db_path", str(db))
    with patch("app.backup.httpx.Client") as client:
        backup.run_startup_backup()
        client.assert_not_called()


def test_periodic_loop_runs_until_stopped():
    import threading

    calls = []
    stop = threading.Event()

    def run():
        calls.append(1)
        if len(calls) == 3:
            stop.set()

    backup._backup_loop(stop, 0.001, run=run)
    assert len(calls) == 3


def test_periodic_loop_survives_a_failing_run():
    import threading

    calls = []
    stop = threading.Event()

    def run():
        calls.append(1)
        if len(calls) == 2:
            stop.set()
        raise RuntimeError("disk full")

    backup._backup_loop(stop, 0.001, run=run)
    assert len(calls) == 2


def test_start_periodic_backup_is_off_at_zero_interval(monkeypatch):
    monkeypatch.setattr(settings, "backup_mode", "folder")
    assert backup.start_periodic_backup(interval_hours=0) is None
    monkeypatch.setattr(settings, "backup_mode", "off")
    assert backup.start_periodic_backup(interval_hours=1) is None
