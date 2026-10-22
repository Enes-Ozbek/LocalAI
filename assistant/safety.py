"""Hard rules that hold whatever the model says. Used by the app and by the dataset checker."""

import os
import re
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath

HOME = Path.home()
PROTECTED = [
    Path(os.environ.get("SystemRoot", r"C:\Windows")),
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    Path(os.environ.get("ProgramData", r"C:\ProgramData")),
    HOME / "AppData",
]
RECENT_DAYS = 14  # never delete a file changed in the last 2 weeks unless it is an exact duplicate

NO_DELETE = re.compile(r"\b(don'?t|do not|never|without|no)\s+(delete|deleting|remove|removing|deletion)\b", re.I)
NO_CHANGE = re.compile(r"\b(don'?t|do not)\s+(change|touch|move)\b|\b(just|only)\s+tell me\b", re.I)
CHANGE_WORDS = ("organize", "organise", "sort", "clean", "tidy", "arrange", "group", "rename", "delete",
                "move", "declutter", "fix", "put")


def is_protected(path: Path) -> bool:
    path = path.resolve()
    if path == HOME.resolve() or len(path.parts) <= 1:
        return True  # never reorganize the whole home folder or a drive root
    for root in PROTECTED:
        try:
            path.relative_to(root.resolve())
            return True
        except ValueError:
            pass
    return False


def wants_changes(user_text: str) -> bool:
    """True if the request asks for changes ('organzie' counts too), not only for a description."""
    if NO_CHANGE.search(user_text):
        return False
    return any(w.startswith(k) or (len(w) >= 5 and SequenceMatcher(None, w, k).ratio() >= 0.8)
               for w in re.findall(r"[a-z]+", user_text.lower()) for k in CHANGE_WORDS)


def names_folder(folder: str, user_text: str) -> bool:
    """True if the request names this folder (typos allowed: 'downlods' -> Downloads).
    Stops the model from choosing a folder the user never mentioned."""
    target = re.findall(r"\w+", PurePosixPath(folder.replace("\\", "/").rstrip("/")).name.lower())
    words = re.findall(r"\w+", user_text.lower())
    n = len(target)
    return n > 0 and any(SequenceMatcher(None, " ".join(words[i:i + n]), " ".join(target)).ratio() >= 0.8
                         for i in range(len(words) - n + 1))


def clean_changes(changes: list, folder: str) -> list:
    """Fixes harmless slips before the checks: empty fields, a missing reason, and a target that repeats
    the folder's own path ('Pictures/Phone/2024' while organizing Pictures/Phone means '2024')."""
    prefix = folder.replace("\\", "/").strip("/ ").lower() + "/"
    out = []
    for c in changes:
        if not isinstance(c, dict):
            continue
        c = {k: v for k, v in c.items() if v not in ("", None)}
        c.setdefault("reason", "no reason given")
        to = (c.get("to_folder") or "").replace("\\", "/").strip("/ ")
        if (to.lower() + "/").startswith(prefix):
            to = to[len(prefix):]
        if to:
            c["to_folder"] = to
        else:
            c.pop("to_folder", None)
        out.append(c)
    return out


def check_changes(changes: list, sheets: list, user_text: str) -> tuple:
    """Splits the model's changes into (allowed, blocked). Blocked entries get a 'blocked' reason."""
    facts = {s["file"]: s for s in sheets}
    no_delete = bool(NO_DELETE.search(user_text))
    allowed, blocked, seen = [], [], set()
    for c in changes:
        problem = _problem(c, facts, seen, no_delete)
        if problem:
            blocked.append({**c, "blocked": problem})
        else:
            allowed.append(c)
            seen.add(c["file"])
    # Never delete every copy of a file: when the original and all its exact copies would go,
    # keep the original.
    copies = {}
    for s in sheets:
        copies.setdefault(s.get("duplicate_of") or s["file"], set()).add(s["file"])
    deleted = {c["file"] for c in allowed if c["action"] == "delete"}
    last_copy = {orig for orig, files in copies.items() if len(files) > 1 and files <= deleted}
    for c in [c for c in allowed if c["action"] == "delete" and c["file"] in last_copy]:
        allowed.remove(c)
        blocked.append({**c, "blocked": "this would delete every copy of this file"})
    return allowed, blocked


def _problem(c: dict, facts: dict, seen: set, no_delete: bool) -> str:
    name, action = c.get("file"), c.get("action")
    if name not in facts:
        return "no such file in this folder"
    if name in seen:
        return "file changed twice"
    if action not in ("move", "rename", "delete"):
        return f"unknown action {action!r}"
    if action == "move" and not c.get("to_folder"):
        return "move without a target folder"
    if action == "rename" and not c.get("new_name"):
        return "rename without a new name"
    new = c.get("new_name")
    if new:
        if "/" in new or "\\" in new or new.strip() in ("", ".", ".."):
            return "new name must be a plain file name"
        if PurePosixPath(new).suffix.lower() != PurePosixPath(name).suffix.lower():
            return "new name changes the file extension"
    to = (c.get("to_folder") or "").replace("\\", "/")
    if to and (to.startswith("/") or ":" in to or ".." in to.split("/")):
        return "target must be a subfolder of this folder"
    if action == "delete":
        if no_delete:
            return "you asked not to delete anything"
        s = facts[name]
        if s["age_days"] < RECENT_DAYS and "duplicate_of" not in s:
            return f"changed in the last {RECENT_DAYS} days"
    return ""
