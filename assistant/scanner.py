"""Turns a folder into what the model sees: its subfolders and a fact sheet per file."""

import hashlib
import os
from collections import defaultdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path

MAX_FILES = 40      # files per model request: more makes the prompt too long for a 4B model (bigger folders go in parts)
TEXT_CHARS = 160    # how much of each document's text the model sees

FILE_TYPES = {
    "images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic"},
    "videos": {".mp4", ".mkv", ".avi", ".mov", ".wmv"},
    "audio": {".mp3", ".wav", ".flac", ".m4a"},
    "documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".odt", ".rtf"},
    "spreadsheets": {".xls", ".xlsx", ".csv", ".ods"},
    "presentations": {".ppt", ".pptx", ".odp"},
    "archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "installers": {".exe", ".msi"},
    "code": {".py", ".js", ".java", ".c", ".cpp", ".cs", ".html", ".css", ".json"},
}
TEXT_EXTS = {".txt", ".md", ".csv", ".py", ".json", ".html", ".log"}


def type_of(path: Path) -> str:
    ext = path.suffix.lower()
    for name, exts in FILE_TYPES.items():
        if ext in exts:
            return name
    return "other"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def text_start(path: Path, chars: int = TEXT_CHARS) -> str:
    ext = path.suffix.lower()
    text = ""
    try:
        if ext in TEXT_EXTS:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read(chars * 4)
        elif ext == ".pdf":
            from pypdf import PdfReader
            text = PdfReader(path).pages[0].extract_text() or ""
        elif ext == ".docx":
            import docx
            text = " ".join(p.text for p in docx.Document(path).paragraphs[:20])
    except Exception:
        return ""
    return " ".join(text.split())[:chars]


def photo_taken(path: Path) -> str:
    """Date the photo was taken (EXIF), if Pillow is installed and the photo has it."""
    try:
        from PIL import Image
        raw = Image.open(path).getexif().get(306)  # 306 = DateTime
        return raw[:10].replace(":", "-") if raw else ""
    except Exception:
        return ""


def files_in(folder: Path) -> list:
    """Every file directly in the folder, by name, without hidden and system files."""
    return sorted((p for p in folder.iterdir() if p.is_file() and not p.name.startswith(("~$", "."))
                   and p.name.lower() != "desktop.ini"), key=lambda p: p.name.lower())


def subfolders_of(folder: Path) -> list:
    return sorted((p.name for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")), key=str.lower)


def find_duplicates(files: list) -> dict:
    """{copy: name of the original} for exact duplicates: same size first (cheap), then same hash.
    The oldest copy is the original."""
    by_size = defaultdict(list)
    for p in files:
        by_size[p.stat().st_size].append(p)
    duplicate_of = {}
    for group in by_size.values():
        if len(group) > 1:
            seen = {}
            for p in sorted(group, key=lambda p: p.stat().st_mtime):
                h = sha256(p)
                if h in seen:
                    duplicate_of[p] = seen[h].name
                else:
                    seen[h] = p
    return duplicate_of


def fact_sheet(p: Path, duplicate_of: dict, now: datetime = None, text: bool = True) -> dict:
    """What the model and the safety rules know about one file. text=False skips the slow part
    (reading documents and photos), for files only the rules need."""
    st = p.stat()
    modified = datetime.fromtimestamp(st.st_mtime)
    s = {"file": p.name, "type": type_of(p), "size_kb": round(st.st_size / 1024, 1),
         "modified": modified.strftime("%Y-%m-%d"), "age_days": ((now or datetime.now()) - modified).days}
    if p in duplicate_of:
        s["duplicate_of"] = duplicate_of[p]
    if text:
        if s["type"] == "images":
            taken = photo_taken(p)
            if taken:
                s["taken"] = taken
        start = text_start(p)
        if start:
            s["text_start"] = start
    return s


def scan(folder: Path, display_name: str, files: list = None, duplicate_of: dict = None,
         subfolders: list = None) -> dict:
    """The tool result for inspect_folder: the folder's first MAX_FILES files, or the given files
    (one part of a big folder, with duplicates found across the whole folder and the subfolders so far)."""
    everything = files_in(folder) if files is None else None
    shown = everything[:MAX_FILES] if files is None else files
    if duplicate_of is None:
        duplicate_of = find_duplicates(shown)
    now = datetime.now()
    sheets = [fact_sheet(p, duplicate_of, now) for p in shown]
    result = {"folder": display_name, "subfolders": subfolders_of(folder) if subfolders is None else subfolders,
              "files": sheets}
    if files is None and len(everything) > MAX_FILES:
        result["more_files"] = len(everything) - MAX_FILES
    return result


# Where Windows keeps each user folder (HKCU\...\Explorer\User Shell Folders): OneDrive can move Desktop and
# Documents, and Windows in another language names them differently (OneDrive\Masaüstü)
SHELL_FOLDERS = {"Desktop": "Desktop", "Documents": "Personal", "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
                 "Pictures": "My Pictures", "Music": "My Music", "Videos": "My Video"}


@lru_cache(maxsize=1)
def known_folders() -> dict:
    """{'Desktop': its real path, ...} for the user folders that exist."""
    home, out = Path.home(), {}
    for name, value in SHELL_FOLDERS.items():
        path = None
        if os.name == "nt":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                    path = Path(os.path.expandvars(winreg.QueryValueEx(key, value)[0]))
            except OSError:
                path = None
        if path is None or not path.is_dir():
            path = home / name
        if path.is_dir():
            out[name] = path
    return out


def resolve_folder(name: str, base: Path) -> Path:
    """'Downloads/New folder1', '~/Desktop' or a full path -> an absolute folder path.
    Relative names are looked up under `base` (the user's home folder, or a sandbox for tests)."""
    name = name.strip().strip('"').replace("\\", "/")
    if name.startswith("~"):
        return Path(os.path.expanduser(name)).resolve()
    p = Path(name)
    if p.is_absolute() or (len(name) > 1 and name[1] == ":"):
        return p.resolve()
    # Match each part case-insensitively ("downloads/new folder1" -> "Downloads/New folder1").
    current = base
    for i, part in enumerate(Path(name).parts):
        exact = current / part
        if exact.exists():
            current = exact
            continue
        matches = [c for c in current.iterdir() if c.name.lower() == part.lower()] if current.is_dir() else []
        if not matches and i == 0 and base.resolve() == Path.home().resolve():
            known = {k.lower(): v for k, v in known_folders().items()}.get(part.lower())
            if known:  # 'Desktop' when Windows keeps it in OneDrive
                current = known
                continue
        current = matches[0] if matches else exact
    return current.resolve()
