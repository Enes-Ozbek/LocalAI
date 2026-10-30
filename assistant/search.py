"""Finds files: 'find my CV', 'where are the pdfs from last month', 'find photos bigger than 5 MB in downloads'.

The chat model isn't needed: the request is read into a filter (filters.py) plus search words, and every file
in the user's folders (or in the folder the request names, with its subfolders) is scored on its name, its
folders and the start of its text. When an embedding model runs on this PC (llm.embed), files are also matched
by meaning, so 'resume' finds a CV. Read-only: the results can be deleted or moved afterwards through the usual
preview, Apply and Undo.

What has been read from documents is kept in a small index (INDEX), so the next search is quicker.
"""

import math
import os
import re
import sqlite3
import time
from array import array
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from . import filters, llm, safety, scanner

SEARCH = re.compile(r"\b(find|search|locate|where\s+(?:is|are|did|was|were|do)|where's|look(?:ing)?\s+for|"
                    r"which\s+files?|show\s+me\s+(?:all|every))\b", re.I)
# 'where did I put my CV' asks where something is; 'put' doesn't make it a request for changes
WHERE_DID = re.compile(r"\bwhere\s+(did|do|have)\s+i\s+(put|save|saved|leave|left|keep|store|stored|download|downloaded)\b", re.I)
USER_FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos", "OneDrive")  # never kinds of file
SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "site-packages", "$recycle.bin",
             "system volume information", "appdata", ".git"}
MAX_RESULTS = 50
MAX_FILES_WALKED = 200_000
TEXT_CHARS = 600            # how much of a document search reads
TEXT_MAX_MB = 25            # bigger documents are searched by name only
READ_BUDGET = 15            # seconds per search for reading documents not in the index yet; the rest wait for the next
EMBED_BUDGET = 20           # the same for meanings
SEMANTIC_MIN = 0.55         # below this cosine similarity, a meaning match counts for nothing
SEMANTIC_SHOW = 0.65        # a file that only matches by meaning is shown from here up
INDEX = Path.home() / ".local-assistant" / "search.sqlite"
READABLE = scanner.TEXT_EXTS | {".pdf", ".docx"}
STOP = {"find", "search", "locate", "where", "is", "are", "was", "were", "did", "do", "does", "have", "i", "me", "my",
        "the", "a", "an", "all", "any", "every", "file", "files", "for", "look", "looking", "show", "which", "that",
        "with", "about", "in", "on", "from", "of", "to", "and", "or", "it", "its", "put", "save", "saved", "leave",
        "left", "keep", "store", "stored", "download", "downloaded", "folder", "folders", "please", "can", "you",
        "some", "something", "there", "this", "these", "those", "what", "named", "called", "contain", "contains",
        "containing", "mention", "mentions", "mentioning", "inside", "under", "within", "pc", "computer", "s"}
SYNONYMS = {"cv": ("resume", "curriculum"), "resume": ("cv", "curriculum"), "invoice": ("bill", "fatura", "receipt"),
            "bill": ("invoice", "fatura"), "bills": ("invoice", "fatura"), "receipt": ("invoice", "fiş"),
            "photo": ("img", "image"), "picture": ("img", "image"), "slides": ("presentation", "lecture"),
            "homework": ("hw", "assignment"), "assignment": ("homework", "hw"), "notes": ("note",)}


def wants_search(user_text: str) -> bool:
    """True for 'find my CV', 'where did I put the lecture notes?'; not for 'find the duplicates and delete them'."""
    return bool(SEARCH.search(user_text)) and not safety.wants_changes(WHERE_DID.sub("where", user_text))


def find(user_text: str, base: Path, progress=None) -> dict:
    """{'text', 'scope' (the folder named, or None), 'matches': [...], 'seconds'}"""
    t0 = time.perf_counter()
    roots, named = _roots(user_text, base)
    tops = [p.name for p in roots] + [name for name, _ in _tops(base)]
    wanted = filters.parse(user_text, tuple(tops) + USER_FOLDERS)
    if named:  # 'in downloads/new folder1' says where to look, not what
        place = {w for part in Path(_show(named, base)).parts for w in re.findall(r"[a-z0-9]+", part.lower())}
        wanted.words = [w for w in wanted.words if w not in place]
    keywords = _keywords(wanted.words)
    where = _show(named, base) if named else "your folders"
    if not keywords and wanted.empty():
        return {"text": "What should I look for? Give a name, a word inside the file, a kind of file "
                        "(photos, pdfs), a date or a size.", "scope": named, "matches": [], "seconds": []}
    if progress:
        progress(f"Searching {where}…")
    now = datetime.now()
    candidates = []
    for p, st in _walk(roots):
        sheet = _sheet(p, st, now)
        candidates.append((p, st, sheet))
    if "duplicates" in wanted.kinds:
        dup = scanner.find_duplicates([p for p, _, _ in candidates])
        for p, _, sheet in candidates:
            if p in dup:
                sheet["duplicate_of"] = dup[p]
    candidates = [c for c in candidates if wanted.matches(c[0], c[2])]

    with _Index() as index:
        unread = 0
        if keywords:
            unread = index.read_texts([(p, st) for p, st, _ in candidates if p.suffix.lower() in READABLE], progress)
        hits = []
        for p, st, sheet in candidates:
            text = index.text(p, st) if keywords and p.suffix.lower() in READABLE else ""
            score, matched, why, snippet = _score(p, base, text, keywords) if keywords else (0, 0, "", "")
            hits.append({"path": p, "st": st, "sheet": sheet, "score": score, "matched": matched, "why": why,
                         "snippet": snippet, "meaning": 0.0})
        meaning_used = False
        if keywords:
            meaning_used = _add_meaning(index, hits, " ".join(wanted.words), progress)

    if keywords:
        hits = [h for h in hits if h["matched"] or h["meaning"] >= SEMANTIC_SHOW]
        hits.sort(key=lambda h: (h["matched"], h["score"] + _bonus(h["meaning"]), h["st"].st_mtime), reverse=True)
    elif wanted.min_kb is not None:
        hits.sort(key=lambda h: h["st"].st_size, reverse=True)
    else:
        hits.sort(key=lambda h: h["st"].st_mtime, reverse=True)
    total = len(hits)
    matches = [_match(h, base) for h in hits[:MAX_RESULTS]]

    if keywords:
        what = " ".join(f"“{k}”" for k in keywords[:4]) + ("" if wanted.empty() else " among " + wanted.describe())
        text = f"Found {total} file{'s' if total != 1 else ''} matching {what} in {where}." if total else \
            f"No files matching {what} in {where}."
    else:
        what = wanted.describe()
        text = f"{what[0].upper() + what[1:]} in {where}: {total or 'none'} found."
    if total > MAX_RESULTS:
        text += f" Here are the best {MAX_RESULTS}; add a word, a date or a folder to narrow it down."
    if unread:
        text += f" I haven't read the text of {unread} document{'s' if unread != 1 else ''} yet; " \
                "search again to include them."
    if keywords and not meaning_used:
        text += " (Matching by meaning is off because no embedding model is running.)"
    return {"text": text, "scope": named, "matches": matches, "seconds": [round(time.perf_counter() - t0, 2)]}


def _tops(base: Path) -> list:
    """(name, folder) for the folders a request can name: the folders in base, and in the home folder also the
    user folders wherever Windows keeps them ('Desktop' may be OneDrive\\Masaüstü)."""
    out = [(p.name, p) for p in sorted(base.iterdir(), key=lambda p: p.name.lower())
           if p.is_dir() and not p.name.startswith(".") and p.name.lower() not in SKIP_DIRS
           and not safety.is_protected(p) and not safety.names_system_folder(p.name)]
    if base.resolve() == safety.HOME.resolve():
        out = list(scanner.known_folders().items()) + out
    return out


def _roots(user_text: str, base: Path) -> tuple:
    """The folders to search: ([folder], folder) when the request names one after in/on/from ('in downloads',
    'on my desktop', 'in downloads/new folder1'), otherwise the user's folders and None."""
    tops = _tops(base)
    places = [m.end() for m in re.finditer(r"\b(?:in|inside|from|on|under|within)\s+(?:my\s+|the\s+)?", user_text, re.I)]
    phrases = [" ".join(re.findall(r"[\w.-]+", user_text[i:])[:5]) for i in places]
    for name, top in tops:
        if any(safety.names_folder(name, ph) and _starts_with(ph, name) for ph in phrases):
            subs = [d for d in top.iterdir() if d.is_dir() and not d.name.startswith(".")]
            for sub in subs:  # 'in downloads/new folder1' or 'in new folder1 in downloads'
                if safety.names_folder(sub.name, user_text) and \
                        re.search(re.escape(sub.name.lower()), user_text.lower()):
                    return [sub], sub
            return [top], top
    if base.resolve() == safety.HOME.resolve():
        roots = list(scanner.known_folders().values()) + [p for p in base.iterdir()
                                                          if p.is_dir() and p.name.lower().startswith("onedrive")]
    else:
        roots = [p for _, p in tops]
    roots = [r.resolve() for r in roots]
    return [r for r in dict.fromkeys(roots) if not any(o != r and r.is_relative_to(o) for o in roots)], None


def _starts_with(phrase: str, name: str) -> bool:
    """'downloads and delete' starts with the folder 'Downloads' (typos allowed); 'documents about taxes' doesn't
    start with 'Desktop'."""
    n = len(re.findall(r"\w+", name))
    head = " ".join(re.findall(r"\w+", phrase.lower())[:n])
    return SequenceMatcher(None, head, " ".join(re.findall(r"\w+", name.lower()))).ratio() >= 0.8


def _walk(roots: list):
    """(path, stat) for every file under the roots, without hidden, system and program folders."""
    count, stack = 0, list(roots)
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            name = e.name
            if name.startswith((".", "~$")) or name.lower() == "desktop.ini":
                continue
            try:
                if getattr(e, "is_junction", lambda: False)():  # old Windows links like Documents\My Music
                    continue
                if e.is_dir(follow_symlinks=False):
                    if name.lower() not in SKIP_DIRS:
                        stack.append(Path(e.path))
                elif e.is_file(follow_symlinks=False):
                    count += 1
                    if count > MAX_FILES_WALKED:
                        return
                    yield Path(e.path), e.stat()
            except OSError:
                continue


def _sheet(p: Path, st, now: datetime) -> dict:
    modified = datetime.fromtimestamp(st.st_mtime)
    return {"file": p.name, "type": scanner.type_of(p), "size_kb": round(st.st_size / 1024, 1),
            "modified": modified.strftime("%Y-%m-%d"), "age_days": (now - modified).days}


def _stem(w: str) -> str:
    for end in ("'s", "ies", "es", "s"):
        if w.endswith(end) and len(w) - len(end) >= 3:
            return w[:-len(end)] + ("y" if end == "ies" else "")
    return w


def _keywords(words: list) -> list:
    out = []
    for w in words:
        if w not in STOP and len(w) > 1 and not w.isdigit() and w not in out:
            out.append(w)
    return out


def _tokens(text: str) -> list:
    """'CV_Enes_2025.docx' -> ['cv', 'enes', '2025', 'docx']; 'LectureNotes' -> ['lecture', 'notes']."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return [t.lower() for t in re.findall(r"[A-Za-z]+|\d+", text)]


def _score(p: Path, base: Path, text: str, keywords: list) -> tuple:
    """(score, how many search words matched, why, snippet)"""
    name_tokens = _tokens(p.stem)
    stem_tokens = {_stem(t) for t in name_tokens}
    try:
        parents = p.parent.relative_to(base).parts
    except ValueError:
        parents = p.parent.parts[-3:]
    folder_tokens = {_stem(t) for part in parents for t in _tokens(part)}
    low_text = text.lower()
    score, matched, why, snippet = 0.0, 0, [], ""
    for kw in keywords:
        variants = {kw, _stem(kw), *SYNONYMS.get(kw, ())}
        best, reason = 0.0, ""
        if variants & set(name_tokens) or variants & stem_tokens:
            best, reason = 3, "name"
        elif any(len(v) >= 3 and v in p.stem.lower() for v in variants):
            best, reason = 2, "name"
        elif any(len(v) >= 4 and len(t) >= 4 and SequenceMatcher(None, v, t).ratio() >= 0.85
                 for v in variants for t in name_tokens):
            best, reason = 1.5, "name"
        if best < 1 and variants & folder_tokens:
            best, reason = 1, "folder"
        if low_text:
            m = next((m for v in variants if len(v) >= 2
                      for m in [re.search(r"(?<!\w)" + re.escape(v) + r"\w*", low_text)] if m), None)
            if m:
                if best < 1:
                    best, reason = 1, "text"
                if not snippet:
                    snippet = _around(text, m.start())
        if best:
            score += best
            matched += 1
            if reason not in why:
                why.append(reason)
    labels = {"name": "name", "folder": "folder name", "text": "text inside"}
    return score, matched, ("matches the " + " and ".join(labels[w] for w in why)) if why else "", snippet


def _around(text: str, i: int, width: int = 90) -> str:
    start = max(0, i - width // 3)
    return ("…" if start else "") + text[start:start + width].strip() + ("…" if start + width < len(text) else "")


def _bonus(similarity: float) -> float:
    return max(0.0, (similarity - SEMANTIC_MIN) / (1 - SEMANTIC_MIN)) * 4


def _add_meaning(index, hits: list, query: str, progress) -> bool:
    """Fills each hit's 'meaning' (cosine similarity to the request) when an embedding model is running.
    Returns False when it isn't."""
    q = llm.embed(["search_query: " + query])
    if not q:
        return False
    qv = q[0]
    docs = [(h, _document(h["path"], index.text(h["path"], h["st"]) if h["path"].suffix.lower() in READABLE else ""))
            for h in hits]
    vectors = index.vectors([(h["path"], h["st"], d) for h, d in docs], progress)
    for h, _ in docs:
        v = vectors.get(str(h["path"]))
        if v:
            h["meaning"] = _cosine(qv, v)
            if not h["matched"] and h["meaning"] >= SEMANTIC_SHOW:
                h["why"] = "similar meaning"
    return True


def _document(p: Path, text: str) -> str:
    name = " ".join(_tokens(p.stem))
    return f"search_document: {name}. Folder: {p.parent.name}. {text[:300]}"


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _show(p: Path, base: Path) -> str:
    try:
        rel = p.relative_to(base)
        return str(rel) if str(rel) != "." else p.name
    except ValueError:
        return str(p)


def _match(h: dict, base: Path) -> dict:
    p, sheet = h["path"], h["sheet"]
    return {"path": str(p), "name": p.name, "where": _show(p.parent, base), "size_kb": sheet["size_kb"],
            "modified": sheet["modified"], "why": h["why"], "snippet": h["snippet"]}


class _Index:
    """path -> (size, modified time, start of text, meaning vector), in SQLite next to the undo logs."""

    def __enter__(self):
        INDEX.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(INDEX)
        self.db.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, "
                        "text TEXT, vector BLOB, vector_model TEXT)")
        self.cache = {}
        return self

    def __exit__(self, *exc):
        self.db.commit()
        self.db.close()

    def _row(self, p: Path, st):
        key = str(p)
        if key not in self.cache:
            row = self.db.execute("SELECT size, mtime, text, vector, vector_model FROM files WHERE path = ?",
                                  (key,)).fetchone()
            self.cache[key] = row if row and row[0] == st.st_size and abs(row[1] - st.st_mtime) < 1e-3 else None
        return self.cache[key]

    def text(self, p: Path, st) -> str:
        row = self._row(p, st)
        return (row[2] or "") if row else ""

    def read_texts(self, files: list, progress=None) -> int:
        """Reads the documents not in the index yet, newest first, for up to READ_BUDGET seconds.
        Returns how many are left unread."""
        todo = sorted((f for f in files if self._row(*f) is None), key=lambda f: f[1].st_mtime, reverse=True)
        deadline = time.perf_counter() + READ_BUDGET
        for i, (p, st) in enumerate(todo):
            if time.perf_counter() > deadline:
                return len(todo) - i
            if progress and i % 25 == 0:
                progress(f"Reading documents ({i} of {len(todo)})…")
            text = scanner.text_start(p, TEXT_CHARS) if st.st_size <= TEXT_MAX_MB * 1024 * 1024 else ""
            self.db.execute("INSERT OR REPLACE INTO files (path, size, mtime, text) VALUES (?, ?, ?, ?)",
                            (str(p), st.st_size, st.st_mtime, text))
            self.cache[str(p)] = (st.st_size, st.st_mtime, text, None, None)
        return 0

    def vectors(self, docs: list, progress=None) -> dict:
        """{path: vector} for (path, stat, document text); new ones are made in batches for up to EMBED_BUDGET seconds."""
        out, todo = {}, []
        for p, st, doc in docs:
            row = self._row(p, st)
            if row and row[3] and row[4] == llm.EMBED_MODEL:
                out[str(p)] = array("f", row[3]).tolist()
            else:
                todo.append((p, st, doc))
        deadline = time.perf_counter() + EMBED_BUDGET
        for i in range(0, len(todo), 64):
            if time.perf_counter() > deadline:
                break
            if progress:
                progress(f"Matching by meaning ({i} of {len(todo)})…")
            batch = todo[i:i + 64]
            vecs = llm.embed([d for _, _, d in batch])
            if not vecs:
                break
            for (p, st, _), v in zip(batch, vecs):
                out[str(p)] = v
                row = self._row(p, st)
                self.db.execute("INSERT OR REPLACE INTO files (path, size, mtime, text, vector, vector_model) "
                                "VALUES (?, ?, ?, ?, ?, ?)",
                                (str(p), st.st_size, st.st_mtime, row[2] if row else None,
                                 array("f", v).tobytes(), llm.EMBED_MODEL))
        return out
