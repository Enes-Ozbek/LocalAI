"""Turns approved changes into file operations, runs them, and undoes them.

Deletes go to the Recycle Bin. Every run is written to an undo log first. Undo moves files back and takes
deleted files out of the Recycle Bin again.
"""

import json
import os
import shutil
import time
from pathlib import Path
from urllib.parse import unquote

UNDO_DIR = Path.home() / ".local-assistant" / "undo"


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
    restored, back, missing = 0, 0, []
    for s in reversed(steps):
        if s["op"] == "move" and Path(s["dst"]).exists() and not Path(s["src"]).exists():
            shutil.move(s["dst"], s["src"])
            restored += 1
            for d in s.get("created_dirs", []):
                try:
                    Path(d).rmdir()  # only removes folders we created that are now empty
                except OSError:
                    pass
        elif s["op"] == "recycle":
            if restore_deleted(Path(s["src"]), s.get("at", 0)):
                back += 1
            else:
                missing.append(Path(s["src"]).name)
    log.rename(log.with_suffix(".undone"))
    msg = f"Moved {restored} file(s) back." if restored or not (back or missing) else ""
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
