"""Turns a folder into what the model sees: its subfolders and a fact sheet per file."""

import hashlib
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

MAX_FILES = 40      # more than this per request makes the prompt too long for a 4B model
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


def text_start(path: Path) -> str:
    ext = path.suffix.lower()
    text = ""
    try:
        if ext in TEXT_EXTS:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read(TEXT_CHARS * 4)
        elif ext == ".pdf":
            from pypdf import PdfReader
            text = PdfReader(path).pages[0].extract_text() or ""
        elif ext == ".docx":
            import docx
            text = " ".join(p.text for p in docx.Document(path).paragraphs[:20])
    except Exception:
        return ""
    return " ".join(text.split())[:TEXT_CHARS]


def photo_taken(path: Path) -> str:
    """Date the photo was taken (EXIF), if Pillow is installed and the photo has it."""
    try:
        from PIL import Image
        raw = Image.open(path).getexif().get(306)  # 306 = DateTime
        return raw[:10].replace(":", "-") if raw else ""
    except Exception:
        return ""


def scan(folder: Path, display_name: str) -> dict:
    """The tool result for inspect_folder."""
    entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    subfolders = [p.name for p in entries if p.is_dir() and not p.name.startswith(".")]
    files = [p for p in entries if p.is_file() and not p.name.startswith(("~$", "."))
             and p.name.lower() != "desktop.ini"]
    shown, more = files[:MAX_FILES], max(0, len(files) - MAX_FILES)

    # Exact duplicates: same size first (cheap), then same hash. The oldest copy is the original.
    by_size = defaultdict(list)
    for p in shown:
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

    now = datetime.now()
    sheets = []
    for p in shown:
        st = p.stat()
        modified = datetime.fromtimestamp(st.st_mtime)
        s = {"file": p.name, "type": type_of(p), "size_kb": round(st.st_size / 1024, 1),
             "modified": modified.strftime("%Y-%m-%d"), "age_days": (now - modified).days}
        if p in duplicate_of:
            s["duplicate_of"] = duplicate_of[p]
        if s["type"] == "images":
            taken = photo_taken(p)
            if taken:
                s["taken"] = taken
        text = text_start(p)
        if text:
            s["text_start"] = text
        sheets.append(s)

    result = {"folder": display_name, "subfolders": subfolders, "files": sheets}
    if more:
        result["more_files"] = more
    return result


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
    for part in Path(name).parts:
        exact = current / part
        if exact.exists():
            current = exact
            continue
        matches = [c for c in current.iterdir() if c.name.lower() == part.lower()] if current.is_dir() else []
        current = matches[0] if matches else exact
    return current.resolve()
