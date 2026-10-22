"""Turns approved changes into file operations, runs them, and undoes them.

Deletes go to the Recycle Bin. Every run is written to an undo log first.
"""

import json
import shutil
import time
from pathlib import Path

UNDO_DIR = Path.home() / ".local-assistant" / "undo"


def plan_steps(folder: Path, changes: list) -> list:
    steps, taken = [], set()
    for c in changes:
        src = folder / c["file"]
        if c["action"] == "delete":
            steps.append({"op": "recycle", "src": str(src), "reason": c.get("reason", "")})
            continue
        target_dir = folder / c["to_folder"] if c.get("to_folder") else folder
        dst = _free_name(target_dir / (c.get("new_name") or c["file"]), taken)
        taken.add(dst)
        steps.append({"op": "move", "src": str(src), "dst": str(dst), "reason": c.get("reason", "")})
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
            dst = Path(s["dst"]).relative_to(folder)
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
                send2trash(s["src"])
                done.append(s)
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
    restored, recycled = 0, 0
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
            recycled += 1
    log.rename(log.with_suffix(".undone"))
    msg = f"Moved {restored} file(s) back."
    if recycled:
        msg += f" {recycled} deleted file(s) are in the Recycle Bin; restore them from there."
    return msg
