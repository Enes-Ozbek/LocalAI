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
# 'Program Files' without C:\ would be looked up in the home folder, so system folders are refused by name too
SYSTEM_TOP = {"windows", "program files", "program files (x86)", "programdata"}
SYSTEM_ANY = {"system32", "syswow64", "appdata"}
ONLY_ASKED = "you only asked about "  # why a change to a file the user didn't name is blocked
RECENT_DAYS = 14  # never delete a file changed in the last 2 weeks unless it is an exact duplicate
TEMP_ENDINGS = (".tmp", ".crdownload", ".part")

NO_DELETE = re.compile(r"\b(don'?t|do not|never|without|no)\s+(delete|deleting|remove|removing|deletion)\b", re.I)
NO_CHANGE = re.compile(r"\b(don'?t|do not)\s+(change|touch|move)\b|\b(just|only)\s+tell me\b", re.I)
DUPLICATE_CLAIM = re.compile(r"\b(duplicates?|identical|exact copy)\b", re.I)
TEMP_CLAIM = re.compile(r"\b(temp|temporary|corrupt|corrupted|broken|incomplete|unfinished)\b", re.I)
CHANGE_WORDS = ("organize", "organise", "sort", "clean", "tidy", "arrange", "group", "rename", "delete",
                "move", "declutter", "fix", "put")
FOLDER_WORDS = ("organize", "organise", "sort", "clean", "tidy", "arrange", "group", "declutter")  # whole folder
# 'the last file', 'the newest download', 'that file': one file picked by position, without its name
POINTS_AT_ONE = re.compile(r"\b(last|newest|latest|most recent|first|oldest|biggest|largest|smallest)\s+"
                           r"(file|one|download|document|photo|pdf)\b|\b(this|that)\s+file\b", re.I)


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


def names_system_folder(folder: str) -> bool:
    """True for 'Program Files', 'windows/system32', 'AppData/Local' and the like, written without a drive."""
    parts = [p.lower() for p in PurePosixPath(folder.replace("\\", "/").strip("/ ")).parts]
    return bool(parts) and (parts[0] in SYSTEM_TOP or any(p in SYSTEM_ANY for p in parts))


def wants_changes(user_text: str) -> bool:
    """True if the request asks for changes ('organzie' counts too), not only for a description."""
    if NO_CHANGE.search(user_text):
        return False
    return _has_word(user_text, CHANGE_WORDS)


def whole_folder(user_text: str) -> bool:
    """True for requests about the whole folder ('organize downloads'), not about some files in it."""
    return _has_word(user_text, FOLDER_WORDS)


def _has_word(user_text: str, keys: tuple) -> bool:
    return any(w.startswith(k) or (len(w) >= 5 and SequenceMatcher(None, w, k).ratio() >= 0.8)
               for w in re.findall(r"[a-z]+", user_text.lower()) for k in keys)


BY_POSITION = {"last": "newest", "newest": "newest", "latest": "newest", "most recent": "newest",
               "first": "oldest", "oldest": "oldest", "biggest": "biggest", "largest": "biggest",
               "smallest": "smallest"}


def file_by_position(user_text: str, folder: Path, names: list) -> tuple:
    """('thesis.docx', 'newest') when the request picks one file by position ('delete the last file') and
    names no file; otherwise None. Newest and oldest go by the real modified time, biggest and smallest by size."""
    m = POINTS_AT_ONE.search(user_text)
    if not names or not m or not m.group(1) or asked_files(user_text, names):
        return None
    which = BY_POSITION[" ".join(m.group(1).lower().split())]
    stat = {n: (folder / n).stat() for n in names}
    key = (lambda n: stat[n].st_mtime) if which in ("newest", "oldest") else (lambda n: stat[n].st_size)
    pick = max(names, key=key) if which in ("newest", "biggest") else min(names, key=key)
    return pick, which


def fill_in(user_text: str, name: str) -> str:
    """'delete the last file in downloads' -> 'delete thesis.docx in downloads': the model handles a named file
    well, but was never trained on 'the last file'."""
    return re.sub(r"\b(the\s+)?(" + POINTS_AT_ONE.pattern + ")", lambda m: name, user_text, count=1, flags=re.I)


def asked_files(user_text: str, names: list) -> set:
    """The files the request names: the full name, or the name without its extension when that has
    5+ characters and isn't a plain word ('delete thesis_final_REAL', but 'my lecture notes' doesn't name
    notes.txt). Whole words only, so 'downloads' doesn't name download.tmp, and 'the newest download' is a
    position, not a name."""
    text, asked = POINTS_AT_ONE.sub(" ", user_text.lower()), set()
    for name in sorted(names, key=len, reverse=True):  # full names first, then they can't match as a stem
        pattern = r"(?<!\w)" + re.escape(name.lower()) + r"(?!\w)"
        if re.search(pattern, text):
            asked.add(name)
            text = re.sub(pattern, " ", text)
    for name in names:
        stem = PurePosixPath(name).stem.lower()
        if name not in asked and len(stem) >= 5 and re.search(r"[^a-z]", stem) \
                and re.search(r"(?<!\w)" + re.escape(stem) + r"(?!\w)", text):
            asked.add(name)
    return asked


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


def copy_groups(sheets: list) -> dict:
    """{original: {original and its exact copies}} from the scanner's duplicate_of flags."""
    copies = {}
    for s in sheets:
        copies.setdefault(s.get("duplicate_of") or s["file"], set()).add(s["file"])
    return copies


def check_changes(changes: list, sheets: list, user_text: str, picked: set = frozenset()) -> tuple:
    """Splits the model's changes into (allowed, blocked). Blocked entries get a 'blocked' reason.
    picked: files the user asked to delete by kind ('delete all .tmp files'); like a named file, they may be
    deleted even if they changed recently."""
    facts = {s["file"]: s for s in sheets}
    copies = copy_groups(sheets)
    has_copy = {f for files in copies.values() if len(files) > 1 for f in files}
    no_delete = bool(NO_DELETE.search(user_text))
    # A request that names files and isn't about the whole folder may only change those files.
    asked = asked_files(user_text, list(facts))
    only = asked if asked and not whole_folder(user_text) else None
    allowed, blocked, seen = [], [], set()
    for c in changes:
        problem = _problem(c, facts, seen, no_delete, has_copy, asked | set(picked))
        if not problem and only is not None and c["file"] not in only:
            problem = ONLY_ASKED + ", ".join(sorted(only))
        if problem:
            blocked.append({**c, "blocked": problem})
        else:
            allowed.append(c)
            seen.add(c["file"])
    # Never delete every copy of a file: when the original and all its exact copies would go,
    # keep the original.
    deleted = {c["file"] for c in allowed if c["action"] == "delete"}
    last_copy = {orig for orig, files in copies.items() if len(files) > 1 and files <= deleted}
    for c in [c for c in allowed if c["action"] == "delete" and c["file"] in last_copy]:
        allowed.remove(c)
        blocked.append({**c, "blocked": "this would delete every copy of this file"})
    return allowed, blocked


def _problem(c: dict, facts: dict, seen: set, no_delete: bool, has_copy: set, asked: set) -> str:
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
        if DUPLICATE_CLAIM.search(c.get("reason") or "") and name not in has_copy:
            return "not a duplicate: the scan found no identical copy of this file"
        s = facts[name]
        if (TEMP_CLAIM.search(c.get("reason") or "") and not name.lower().endswith(TEMP_ENDINGS)
                and s.get("size_kb", 1) >= 1):
            return "not a temporary or broken file: the scan found a normal file"
        # A file the user named is exempt: they asked for it, and the preview and Recycle Bin still protect it.
        if s["age_days"] < RECENT_DAYS and "duplicate_of" not in s and name not in asked:
            return f"changed in the last {RECENT_DAYS} days"
    return ""
