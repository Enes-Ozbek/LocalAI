"""Undo puts deleted files back from the Recycle Bin (Windows) or the trash (Linux)."""

import struct
import time
from pathlib import Path

from assistant import executor


def test_apply_then_undo_brings_deleted_files_back(base):
    files = [base / "Downloads" / "download.tmp", base / "Downloads" / "IMG_2034 (1).jpg"]
    data = [p.read_bytes() for p in files]
    executor.apply([{"op": "recycle", "src": str(p), "reason": "x"} for p in files])
    assert not any(p.exists() for p in files)
    assert executor.undo_last() == "Put 2 deleted file(s) back from the Recycle Bin."
    assert [p.read_bytes() for p in files] == data


def windows_entry(folder: Path, name: str, original: str, deleted: float, version: int = 2):
    """An $I/$R pair as Windows writes it."""
    filetime = int((deleted + 11644473600) * 1e7)
    path = original.encode("utf-16-le") + b"\0\0"
    if version == 2:
        info = struct.pack("<qqqi", 2, 5, filetime, len(original) + 1) + path
    else:
        info = struct.pack("<qqq", 1, 5, filetime) + path.ljust(520, b"\0")
    (folder / f"$I{name}").write_bytes(info)
    (folder / f"$R{name}").write_bytes(b"hello")


def test_windows_recycle_bin_entries_are_read_and_restored(tmp_path, monkeypatch):
    bin_ = tmp_path / "$Recycle.Bin" / "S-1-5-21-1"
    bin_.mkdir(parents=True)
    (tmp_path / "$Recycle.Bin" / "S-1-5-21-2").mkdir()  # another user's folder
    target = tmp_path / "Downloads" / "notes ş.txt"
    now = time.time()
    windows_entry(bin_, "ABC123.txt", str(target), now - 3600)  # an older delete of the same path
    windows_entry(bin_, "DEF456.txt", str(target), now)
    windows_entry(bin_, "GHI789.txt", str(tmp_path / "other.txt"), now, version=1)
    entries = {Path(i.name).name: (w, round(d)) for w, d, _, i in executor._recycle_bin(tmp_path / "$Recycle.Bin")}
    assert entries["$IDEF456.txt"] == (str(target), round(now)) and entries["$IGHI789.txt"][0] == str(tmp_path / "other.txt")
    monkeypatch.setattr(executor, "trash_entries", lambda src: executor._recycle_bin(tmp_path / "$Recycle.Bin"))
    assert executor.restore_deleted(target, since=now - 1)
    assert target.read_bytes() == b"hello" and not (bin_ / "$RDEF456.txt").exists() and not (bin_ / "$IDEF456.txt").exists()
    assert (bin_ / "$RABC123.txt").exists()  # the older one is left alone
    assert not executor.restore_deleted(target, since=now - 1)  # a file is back in its place: nothing is overwritten
    assert not executor.restore_deleted(tmp_path / "other.txt", since=now + 60)  # deleted before this run


def test_undo_says_which_deleted_files_it_could_not_find(base, monkeypatch):
    p = base / "Downloads" / "download.tmp"
    executor.apply([{"op": "recycle", "src": str(p), "reason": "x"}])
    monkeypatch.setattr(executor, "trash_entries", lambda src: iter(()))  # the Recycle Bin was emptied
    assert executor.undo_last().startswith("1 deleted file(s) are not in the Recycle Bin any more")
