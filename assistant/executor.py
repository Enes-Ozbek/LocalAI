"""Turns approved changes into file operations, runs them, and undoes them.

Deletes go to the Recycle Bin. Every run is written to an undo log first. Undo moves files back and takes
deleted files out of the Recycle Bin again.

App folders removed by the leftover cleanup (leftovers.py) go to a quarantine folder instead: a move on the same
drive is instant even for 20 GB, Undo always works, and the Recycle Bin can skip folders that big without asking.
The quarantine is emptied after QUARANTINE_DAYS, or when the user presses Empty now.
"""

import errno
import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path
from urllib.parse import unquote

UNDO_DIR = Path.home() / ".local-assistant" / "undo"
QUARANTINE_DIR = Path.home() / ".local-assistant" / "quarantine"
QUARANTINE_DAYS = 7


def plan_steps(folder: Path, changes: list) -> list:
    steps, taken = [], set()
    for c in changes:
        src = folder / c["file"]
        if c["action"] == "delete":
            steps.append({"op": "recycle", "src": str(src), "reason": c.get("reason", "")})
            continue
        if c.get("to_path"):  # another folder the user named, such as their Documents
            target_dir = Path(c["to_path"])
        else:
            target_dir = folder / c["to_folder"] if c.get("to_folder") else folder
        dst = _free_name(target_dir / (c.get("new_name") or c["file"]), taken)
        taken.add(dst)
        steps.append({"op": "move", "src": str(src), "dst": str(dst), "reason": c.get("reason", "")})
    return steps


def move_steps(files: list, target_dir: Path, reason: str) -> list:
    """Moves of whole paths (search results) into one folder; files already there stay where they are."""
    steps, taken = [], set()
    for src in files:
        if src.parent == target_dir:
            continue
        dst = _free_name(target_dir / src.name, taken)
        taken.add(dst)
        steps.append({"op": "move", "src": str(src), "dst": str(dst), "reason": reason})
    return steps


def _free_name(path: Path, taken: set) -> Path:
    """'a.pdf' -> 'a (2).pdf' if the name is already used, so nothing is ever overwritten."""
    candidate, n = path, 2
    while candidate.exists() or candidate in taken:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def preview(steps: list, folder: Path) -> str:
    lines = []
    for s in steps:
        src = Path(s["src"]).name
        if s["op"] == "recycle":
            lines.append(f"  DELETE  {src}  (to Recycle Bin)  - {s['reason']}")
        else:
            dst = Path(s["dst"])
            dst = dst.relative_to(folder) if dst.is_relative_to(folder) else dst  # full path when it leaves the folder
            verb = "RENAME" if Path(s["dst"]).parent == folder else "MOVE  "
            lines.append(f"  {verb}  {src}  ->  {dst}  - {s['reason']}")
    return "\n".join(lines)


def apply(steps: list) -> Path:
    from send2trash import send2trash
    UNDO_DIR.mkdir(parents=True, exist_ok=True)
    log_path = UNDO_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    done = []
    try:
        for s in steps:
            if s["op"] == "move":
                created = [str(p) for p in _missing_parents(Path(s["dst"]))]
                Path(s["dst"]).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(s["src"], s["dst"])
                done.append({**s, "created_dirs": created})
            else:
                at = time.time()
                send2trash(s["src"])
                done.append({**s, "at": at})
    finally:
        log_path.write_text(json.dumps(done, indent=1, ensure_ascii=False), encoding="utf-8")
    return log_path


def _missing_parents(path: Path) -> list:
    missing = []
    for parent in path.parents:
        if parent.exists():
            break
        missing.append(parent)
    return missing


def undo_last() -> str:
    logs = sorted(UNDO_DIR.glob("*.json")) if UNDO_DIR.exists() else []
    if not logs:
        return "Nothing to undo."
    log = logs[-1]
    steps = json.loads(log.read_text(encoding="utf-8"))
    restored, back, missing, gone = 0, 0, [], []
    for s in reversed(steps):
        if s["op"] == "move" and Path(s["dst"]).exists() and not Path(s["src"]).exists():
            shutil.move(s["dst"], s["src"])
            restored += 1
            for d in s.get("created_dirs", []):
                try:
                    Path(d).rmdir()  # only removes folders we created that are now empty
                except OSError:
                    pass
        elif s["op"] == "quarantine":
            if _unquarantine(s):
                restored += 1
            else:
                gone.append(Path(s["src"]).name)
        elif s["op"] == "recycle":
            if restore_deleted(Path(s["src"]), s.get("at", 0)):
                back += 1
            else:
                missing.append(Path(s["src"]).name)
    log.rename(log.with_suffix(".undone"))
    msg = f"Moved {restored} {'folder' if steps and steps[0]['op'] == 'quarantine' else 'file'}(s) back." \
        if restored or not (back or missing or gone) else ""
    if gone:
        msg += (f" {len(gone)} folder(s) are not in the quarantine any more (emptied, or a folder of that name is "
                f"back in their place): {', '.join(gone)}.")
    if back:
        msg += f" Put {back} deleted file(s) back from the Recycle Bin."
    if missing:
        msg += (f" {len(missing)} deleted file(s) are not in the Recycle Bin any more (emptied, or a file is already "
                f"back in their place): {', '.join(missing)}.")
    return msg.strip()


def restore_deleted(src: Path, since: float = 0) -> bool:
    """Takes a file this app deleted out of the Recycle Bin (or the Linux trash) and puts it back where it was.
    Only an entry for exactly that path, deleted at or after `since`, counts; with several, the newest."""
    if src.exists():
        return False
    best = None  # (deleted at, the trashed file, its info file)
    for found in trash_entries(src):
        where, deleted, item, info = found
        if os.path.normcase(os.path.normpath(where)) == os.path.normcase(os.path.normpath(str(src))) \
                and deleted >= since - 5 and item.exists() and (best is None or deleted > best[0]):
            best = (deleted, item, info)
    if best is None:
        return False
    src.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(best[1]), str(src))
    try:
        best[2].unlink()  # the Recycle Bin's note about the file: without it, no empty entry is left behind
    except OSError:
        pass
    return True


def trash_entries(src: Path):
    """(original path, deleted at, trashed file, info file) for what lies in the trash src would have gone to."""
    if os.name == "nt":
        yield from _recycle_bin(Path(src.anchor) / "$Recycle.Bin")
    else:
        yield from _freedesktop_trash(src)


def _recycle_bin(root: Path):
    """Windows keeps each deleted file as $R<id>.<ext>, with $I<id>.<ext> holding its old path and the time.
    Each user has a folder named after their account id; other users' folders can't be read and are skipped."""
    try:
        folders = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return
    for folder in folders:
        try:
            infos = list(folder.glob("$I*"))
        except OSError:
            continue
        for info in infos:
            try:
                where, deleted = _read_recycle_info(info.read_bytes())
            except (OSError, ValueError):
                continue
            yield where, deleted, info.with_name("$R" + info.name[2:]), info


def _read_recycle_info(data: bytes) -> tuple:
    """An $I file: version (8 bytes), size (8), deletion time as a Windows FILETIME (8), then the path:
    version 1 (Vista, 7) 260 UTF-16 characters, version 2 (8, 10, 11) a length (4 bytes) and the characters."""
    version = int.from_bytes(data[:8], "little")
    filetime = int.from_bytes(data[16:24], "little")
    if version == 1:
        raw = data[24:24 + 520]
    elif version == 2:
        raw = data[28:28 + 2 * int.from_bytes(data[24:28], "little")]
    else:
        raise ValueError("unknown Recycle Bin entry")
    where = raw.decode("utf-16-le").split("\0", 1)[0]
    return where, filetime / 1e7 - 11644473600  # 100 ns steps since 1601 -> seconds since 1970


def _freedesktop_trash(src: Path):
    """Linux: Trash/info/<name>.trashinfo holds Path= and DeletionDate=, the file is Trash/files/<name>. The trash is
    in the home folder, or in .Trash-<uid> at the top of another drive."""
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Trash"
    tops = [home] + [p / f".Trash-{os.getuid()}" for p in src.parents if (p / f".Trash-{os.getuid()}").is_dir()]
    for trash in tops:
        try:
            infos = list((trash / "info").glob("*.trashinfo"))
        except OSError:
            continue
        for info in infos:
            try:
                fields = dict(line.split("=", 1) for line in info.read_text(encoding="utf-8").splitlines() if "=" in line)
                where = unquote(fields["Path"])
                if not where.startswith("/"):
                    where = str(trash.parent / where)
                deleted = time.mktime(time.strptime(fields["DeletionDate"][:19], "%Y-%m-%dT%H:%M:%S"))
            except (OSError, KeyError, ValueError):
                continue
            yield where, deleted, trash / "files" / info.name[:-len(".trashinfo")], info


# ---- the quarantine for app folders (leftover cleanup)

def apply_cleanup(steps: list) -> tuple:
    """Moves each app folder (or, for a cache, everything in it) into a new quarantine folder.
    Returns (undo log, [(folder, why it failed)]). A folder a program still holds open fails on its own and stays
    where it was; the rest go on. Only a plain rename is used, never copy-and-delete, so a folder is never left
    half moved."""
    UNDO_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path, box = UNDO_DIR / f"{stamp}.json", QUARANTINE_DIR / stamp
    done, failed = [], []
    try:
        for k, s in enumerate(steps, 1):
            src, dst = Path(s["src"]), box / str(k) / Path(s["src"]).name
            try:
                if not src.is_dir():
                    raise FileNotFoundError("it isn't there any more")
                dst.parent.mkdir(parents=True, exist_ok=True)
                if s.get("contents_only"):
                    dst.mkdir()
                    moved = []
                    for child in sorted(src.iterdir()):
                        try:
                            _rename(child, dst / child.name)
                            moved.append(child.name)
                        except OSError:
                            pass  # a file the program has open: the rest of the cache still goes
                    if not moved and any(src.iterdir()):
                        raise PermissionError("its files are in use")
                    done.append({**s, "dst": str(dst), "moved": moved})
                else:
                    _rename(src, dst)
                    done.append({**s, "dst": str(dst)})
            except OSError as e:
                failed.append((s.get("folder") or str(src), _why(e)))
    finally:
        log_path.write_text(json.dumps(done, indent=1, ensure_ascii=False), encoding="utf-8")
    return log_path, failed


def _rename(src: Path, dst: Path):
    try:
        os.rename(src, dst)
    except OSError as e:
        # another drive (a sandbox on D:, a test folder): only then is a copy allowed
        if e.errno == errno.EXDEV or getattr(e, "winerror", None) == 17:
            shutil.move(str(src), str(dst))
        else:
            raise


def _why(e: OSError) -> str:
    if isinstance(e, PermissionError) or getattr(e, "winerror", None) in (5, 32):
        return "a program is using it (close it and try again)"
    return str(e.strerror or e)


def _unquarantine(s: dict) -> bool:
    src, dst = Path(s["src"]), Path(s["dst"])
    if not dst.exists():
        return False
    if s.get("contents_only"):
        src.mkdir(parents=True, exist_ok=True)
        for name in s.get("moved", []):
            if (dst / name).exists() and not (src / name).exists():
                _rename(dst / name, src / name)
        _remove_empty(dst)
        return True
    if src.exists():
        return False
    src.parent.mkdir(parents=True, exist_ok=True)
    _rename(dst, src)
    _remove_empty(dst.parent)
    return True


def _remove_empty(folder: Path):
    for d in (folder, folder.parent):
        try:
            d.rmdir()
        except OSError:
            return


def quarantine_size() -> int:
    """Bytes waiting in the quarantine."""
    total = 0
    for root, _, files in os.walk(QUARANTINE_DIR):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def empty_quarantine(older_than_days: float = 0) -> tuple:
    """Deletes quarantined folders for good: all of them, or those older than the given days (the app does that
    with QUARANTINE_DAYS when it starts). Returns (runs emptied, bytes freed). Their undo logs are marked, so
    Undo says plainly that they are gone."""
    if not QUARANTINE_DIR.is_dir():
        return 0, 0
    cutoff, runs, freed = time.time() - older_than_days * 86400, 0, 0
    for box in sorted(QUARANTINE_DIR.iterdir()):
        try:
            made = time.mktime(time.strptime(box.name, "%Y%m%d-%H%M%S"))
        except ValueError:
            made = box.stat().st_mtime
        if not box.is_dir() or made > cutoff:
            continue
        size = sum(os.lstat(os.path.join(r, f)).st_size for r, _, fs in os.walk(box) for f in fs)
        shutil.rmtree(box, **({"onexc": _force} if sys.version_info >= (3, 12) else {"onerror": _force}))
        if not box.exists():
            runs, freed = runs + 1, freed + size
            log = UNDO_DIR / f"{box.name}.json"
            if log.exists():
                log.rename(log.with_suffix(".emptied"))
    return runs, freed


def _force(func, path, _):
    """Read-only files (git keeps many) can't be deleted until they are made writable."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass
