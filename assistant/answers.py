"""Questions about folders that code answers exactly, without the chat model: 'how many files are there?',
'how many pdfs are in downloads', 'how big is my desktop', 'what's the biggest file in archive',
'the 3 newest files in downloads'. A 4B model miscounts long lists, and these need no judgement.

A question that names no folder is about the folder talked about last (agent.Session), and without one about
the user's main folders, each counted with its subfolders. Read-only, like search.
"""

import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import filters, safety, scanner, search

COUNT = re.compile(r"\b(how\s+many|count|number\s+of)\b", re.I)
SIZE = re.compile(r"\b(how\s+(?:big|large|heavy)|how\s+much\s+(?:space|room|storage|disk)|total\s+size|"
                  r"size\s+of)\b", re.I)
WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
POSITION = re.compile(r"\b(?:(\d+|" + "|".join(WORDS) + r")\s+)?(biggest|largest|smallest|newest|latest|"
                      r"most\s+recent|oldest|last|first)\b(?=\s+(?:(?!(?:day|week|month|year)s?\b)\w+\s+)?"
                      r"(files?|ones?|downloads?|documents?|photos?|pictures?|pdfs?|videos?|screenshots?|installers?)\b)",
                      re.I)  # not 'last week photos': that's a date
WHICH = {"biggest": "biggest", "largest": "biggest", "smallest": "smallest", "newest": "newest", "latest": "newest",
         "most recent": "newest", "last": "newest", "oldest": "oldest", "first": "oldest"}
# 'how many photos do I have' is about all folders, even right after talking about one
EVERYWHERE = re.compile(r"\b(do|did)\s+i\s+have\b|\bin\s+(all|total)\b|\baltogether\b|\beverywhere\b|"
                        r"\ball\s+(?:my\s+)?folders\b|\bon\s+(?:my|this)\s+(?:pc|computer|laptop)\b", re.I)
TYPE_NAMES = {"images": "photos", "other": "other files"}
MAX_SHOWN = 20


def wants_answer(user_text: str) -> bool:
    """True for 'how many files are there?', 'how big is downloads', 'what's the newest file in archive';
    not for 'delete the biggest file' or 'how many files can I delete? clean it up'."""
    return bool(COUNT.search(user_text) or SIZE.search(user_text) or POSITION.search(user_text)) \
        and not safety.wants_changes(user_text)


def named_folder(user_text: str, base: Path):
    """The folder a request names, or None: after in/on/from ('in downloads/new folder1'), or anywhere when the
    name isn't also a kind of file ('how big is archive'); 'documents', 'pictures', 'videos' and 'music' count
    as folders only as 'my documents', 'the pictures folder' and the like ('how many documents' means files)."""
    _, named = search._roots(user_text, base)
    if named:
        return named
    low = user_text.lower()
    for name, folder in search._tops(base):
        if not safety.names_folder(name, user_text):
            continue
        words = re.findall(r"\w+", name.lower())
        if not any(w in filters.WORD_KIND or w.rstrip("s") in filters.WORD_KIND for w in words):
            return folder
        if re.search(r"\b(my|the|of|is|does)\s+" + re.escape(name.lower()) + r"\b|\b" + re.escape(name.lower())
                     + r"\s+folder\b", low):
            return folder
    return None


def answer(user_text: str, base: Path, last_folder: Path = None, folder: Path = None) -> dict:
    """{'kind': 'explain' or 'search', 'text', 'folder', 'matches', 'seconds'}. folder: the folder to answer
    about, when the caller already knows it ('and desktop?' after a question about downloads)."""
    t0 = time.perf_counter()
    folder = folder or named_folder(user_text, base)
    if folder is None and last_folder is not None and last_folder.is_dir() and not EVERYWHERE.search(user_text):
        folder = last_folder
    tops = [name for name, _ in search._tops(base)]
    rest = POSITION.sub(" ", SIZE.sub(" ", COUNT.sub(" ", user_text)))  # 'biggest file' isn't 'big files'
    wanted = filters.parse(rest, tuple(tops) + search.USER_FOLDERS, here=(folder.name,) if folder else ())
    what = wanted.describe() if not wanted.empty() else "files"
    now = datetime.now()

    if folder is not None:
        place = search._show(folder, base)
        direct = _sheets([(p, None) for p in scanner.files_in(folder)], now)
        subfolders = scanner.subfolders_of(folder)
        below = _sheets(search._walk([folder / s for s in subfolders]), now)
        _mark_duplicates(wanted, direct + below)
        direct = [x for x in direct if wanted.matches(*x)]
        below = [x for x in below if wanted.matches(*x)]
    else:
        place = "your main folders"
        roots, _ = search._roots("", base)
        groups = {}
        for root in roots:
            groups[_label(root, base)] = _sheets(search._walk([root]), now)
        _mark_duplicates(wanted, [x for g in groups.values() for x in g])
        groups = {k: [x for x in g if wanted.matches(*x)] for k, g in groups.items()}

    m = POSITION.search(user_text)
    if m and not COUNT.search(user_text) and not SIZE.search(user_text):
        many = 5 if m.group(3).lower().endswith("s") else 1  # 'the biggest files': a few
        n = int(m.group(1)) if m.group(1) and m.group(1).isdigit() else WORDS.get((m.group(1) or "").lower(), many)
        which = WHICH[" ".join(m.group(2).lower().split())]
        pool = direct if folder is not None else [x for g in groups.values() for x in g]
        text, matches = _extreme(pool, which, min(n, MAX_SHOWN), what, place, base, folder is None)
        return {"kind": "search", "text": text, "folder": folder, "matches": matches, "seconds": _secs(t0)}
    if SIZE.search(user_text) and not COUNT.search(user_text):
        text = _size_text(direct, below, subfolders, what, place) if folder is not None else \
            _size_overview(groups, what)
    else:
        text = _count_text(direct, below, subfolders, what, place, wanted.empty()) if folder is not None else \
            _count_overview(groups, what)
    return {"kind": "explain", "text": text, "folder": folder, "matches": [], "seconds": _secs(t0)}


def _sheets(found, now: datetime) -> list:
    """[(path, fact sheet)] for (path, stat or None) pairs. The sheet also keeps the exact size and time
    ('bytes', 'mtime') for sorting; dataset/make_questions.py makes the same from its made-up folders."""
    out = []
    for p, st in found:
        try:
            st = st or p.stat()
            out.append((p, {**search._sheet(p, st, now), "bytes": st.st_size, "mtime": st.st_mtime}))
        except OSError:
            continue
    return out


def _mark_duplicates(wanted, items: list):
    if "duplicates" in wanted.kinds:
        dup = scanner.find_duplicates([p for p, _ in items])
        for p, sheet in items:
            if p in dup:
                sheet["duplicate_of"] = dup[p]


def _label(root: Path, base: Path) -> str:
    """'Desktop' for C:\\Users\\me\\OneDrive\\Masaüstü, 'Archive' for sandbox\\Archive."""
    for name, folder in scanner.known_folders().items():
        if base.resolve() == safety.HOME.resolve() and folder.resolve() == root.resolve():
            return name
    return root.name


def _secs(t0: float) -> list:
    return [round(time.perf_counter() - t0, 2)]


def _n(count: int, what: str) -> str:
    """'1 file', '28 files', '1 .pdf file', '3 photos from 2024'."""
    if count == 1:
        what = re.sub(r"\b(files|photos|screenshots|installers|duplicates|videos|documents|spreadsheets|"
                      r"presentations|archives)\b", lambda m: m.group(1)[:-1], what, count=1)
    return f"{count:,} {what}"


def size_text(kb: float) -> str:
    """'2.6 GB', '323.8 MB', '648 KB', '0.6 KB'"""
    if kb >= 1024 * 1024:
        return f"{kb / 1024 / 1024:.1f} GB"
    if kb >= 1024:
        return f"{kb / 1024:.1f} MB"
    return f"{kb:.0f} KB" if kb >= 10 else f"{kb:.1f} KB"


def _mb(items: list) -> str:
    return size_text(sum(s["size_kb"] for _, s in items))


def _kinds(items: list) -> str:
    """'10 documents, 6 photos and 1 installer'"""
    kinds = Counter(s["type"] for _, s in items)
    listed = [_n(n, TYPE_NAMES.get(k, k)) for k, n in kinds.most_common()]
    return listed[0] if len(listed) == 1 else ", ".join(listed[:-1]) + " and " + listed[-1]


def _count_text(direct, below, subfolders, what, place, everything) -> str:
    text = f"{place} has {_n(len(direct), what)}"
    if everything and direct:
        text += f": {_kinds(direct)}, {_mb(direct)} in all"
    text += "."
    if subfolders:
        names = ", ".join(subfolders[:5]) + (" and others" if len(subfolders) > 5 else "")
        text += f" Its {len(subfolders)} subfolder{'s' if len(subfolders) != 1 else ''} ({names}) " \
                f"{'have' if len(subfolders) != 1 else 'has'} " + _n(len(below), what).replace(" ", " more ", 1) + "."
    return text


def _count_overview(groups: dict, what) -> str:
    total = sum(len(g) for g in groups.values())
    parts = ", ".join(f"{k} {len(g):,}" for k, g in sorted(groups.items(), key=lambda kv: -len(kv[1])))
    return f"Your main folders have {_n(total, what)}, counting their subfolders: {parts}. " \
           "Name a folder for more detail, for example 'how many files are in Downloads?'."


def _size_text(direct, below, subfolders, what, place) -> str:
    text = f"The {_n(len(direct), what)} in {place} take {_mb(direct)}."
    if subfolders and below:
        text += f" Its subfolders hold {_n(len(below), what)} more, {_mb(below)}."
    return text


def _size_overview(groups: dict, what) -> str:
    items = [x for g in groups.values() for x in g]
    parts = ", ".join(f"{k} {_mb(g)}" for k, g in
                      sorted(groups.items(), key=lambda kv: -sum(s["size_kb"] for _, s in kv[1])))
    return f"The {_n(len(items), what)} in your main folders take {_mb(items)}, counting their subfolders: {parts}."


def _extreme(pool: list, which: str, n: int, what: str, place: str, base: Path, everywhere: bool) -> tuple:
    if not pool:
        return f"There are no {what} in {place}.", []
    key = (lambda x: x[1]["bytes"]) if which in ("biggest", "smallest") else (lambda x: x[1]["mtime"])
    picked = sorted(pool, key=key, reverse=which in ("biggest", "newest"))[:n]

    def about(p, s):
        where = f" in {search._show(p.parent, base)}" if everywhere else ""
        return f"{p.name}{where} ({size_text(s['size_kb'])}, changed {s['modified']})"
    if len(picked) == 1:
        text = f"The {which} {_n(1, what).split(' ', 1)[1]} in {place} is {about(*picked[0])}."
    else:
        text = f"The {len(picked)} {which} {what} in {place}: " + "; ".join(about(p, s) for p, s in picked) + "."
    matches = [{"path": str(p), "name": p.name, "where": search._show(p.parent, base), "size_kb": s["size_kb"],
                "modified": s["modified"], "why": which, "snippet": ""} for p, s in picked]
    return text, matches


# Tools for the chat model: it decides what to look at ('list Downloads by size, top 5'), code does the exact
# counting and sorting, and the model answers from the result.

SHOW_LIMIT = 50
FIRST = {"size": ("biggest", "smallest"), "date": ("newest", "oldest"), "name": ("Z", "A")}  # desc, asc


def run_tool(name: str, args: dict, base: Path, progress=None) -> tuple:
    """(result for the model, files to show the user, the folder looked at or None). Never changes anything."""
    if name not in ("list_files", "folder_summary", "search_files"):
        return {"error": f"There is no tool called {name}. Use list_files, folder_summary or search_files."}, [], None
    folder_name = str(args.get("folder") or "").strip()
    folder = None
    if folder_name:
        folder = _tool_folder(folder_name, base)
        if folder is None:
            tops = ", ".join(n for n, _ in search._tops(base))
            return {"error": f"There is no folder called '{folder_name}'. The main folders are: {tops}."}, [], None
        if safety.is_protected(folder) or safety.names_system_folder(folder_name):
            return {"error": f"{folder_name} is a protected folder."}, [], None
    if progress:
        progress(f"Looking at {search._show(folder, base) if folder else 'your folders'}…")
    if name == "search_files":
        query = str(args.get("query") or "").strip()
        found = search.find(f"find {query}" + (f" in {search._show(folder, base)}" if folder else ""), base)
        shown = [{"file": m["name"], "folder": m["where"], "size": size_text(m["size_kb"]),
                  "modified": m["modified"]} for m in found["matches"][:10]]
        return {"result": found["text"], "files": shown}, found["matches"], folder
    if name == "folder_summary":
        return _summary(folder, base), [], folder
    return _list(args, folder, base)


def _tool_folder(name: str, base: Path):
    folder = scanner.resolve_folder(name, base)
    if folder.is_dir():
        return folder
    for top_name, top in search._tops(base):  # typos: 'dowloads'
        if safety.names_folder(top_name, name) and search._starts_with(name, top_name):
            return top
    return None


def _number(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def tool_filter(args: dict) -> filters.Filter:
    """The list_files arguments as a file filter."""
    wanted = filters.parse(" ".join(str(args.get(k) or "") for k in ("kind", "extension")))
    wanted.words = []
    if args.get("name_contains"):
        wanted.name_has = [str(args["name_contains"])]
    for key, attr, scale in (("older_than_days", "older_days", 1), ("newer_than_days", "newer_days", 1),
                             ("min_size_mb", "min_kb", 1024), ("max_size_mb", "max_kb", 1024)):
        value = _number(args.get(key))
        if value is not None:
            setattr(wanted, attr, int(value) if scale == 1 else value * scale)
    return wanted


def _list(args: dict, folder, base: Path) -> tuple:
    wanted, now = tool_filter(args), datetime.now()
    if folder is not None:
        items = _sheets([(p, None) for p in scanner.files_in(folder)], now)
        subfolders = scanner.subfolders_of(folder)
        below = _sheets(search._walk([folder / s for s in subfolders]), now)
    else:
        roots, _ = search._roots("", base)
        items, subfolders, below = _sheets(search._walk(roots), now), [], []
    _mark_duplicates(wanted, items + below)
    place = search._show(folder, base) if folder else None
    out, shown = list_result(args, items, below, subfolders, place, lambda p: search._show(p.parent, base))
    matches = [{"path": str(p), "name": p.name, "where": search._show(p.parent, base), "size_kb": s["size_kb"],
                "modified": s["modified"], "why": "", "snippet": ""} for p, s in shown]
    return out, matches, folder


def list_result(args: dict, items: list, below: list, subfolders: list, place, where) -> tuple:
    """(list_files result, the files shown) for [(path, sheet)] directly in the folder and in its subfolders.
    place: the folder's name, None for the user's main folders. where(path): the folder to show for a file."""
    wanted = tool_filter(args)
    if args.get("include_subfolders"):
        items, below = items + below, []
    items = [x for x in items if wanted.matches(*x)]
    below = [x for x in below if wanted.matches(*x)]
    sort_by = args.get("sort_by") if args.get("sort_by") in ("size", "date", "name") else "date"
    order = args.get("order") if args.get("order") in ("asc", "desc") else ("asc" if sort_by == "name" else "desc")
    key = {"size": lambda x: x[1]["bytes"], "date": lambda x: x[1]["mtime"], "name": lambda x: x[0].name.lower()}
    items = sorted(items, key=key[sort_by], reverse=order == "desc")
    limit = max(1, min(int(_number(args.get("limit")) or 10), SHOW_LIMIT))
    shown = items[:limit]
    out = {"folder": place or "the user's main folders, with their subfolders",
           "looked_for": wanted.describe() if not wanted.empty() else "files",
           "matching_files": len(items), "total_size": _mb(items),
           "sorted_by": f"{sort_by}, {FIRST[sort_by][order == 'asc']} first",
           "files": [{"file": p.name, "folder": where(p), "size": size_text(s["size_kb"]),
                      "modified": s["modified"]} for p, s in shown]}
    if below:
        out["not_counted"] = f"{len(below)} more in its subfolders ({', '.join(subfolders[:5])}); " \
                             "set include_subfolders to count them"
    return out, shown


def _summary(folder, base: Path) -> dict:
    now = datetime.now()
    if folder is None:
        roots, _ = search._roots("", base)
        return overview_result([(_label(root, base), _sheets(search._walk([root]), now)) for root in roots])
    items = _sheets([(p, None) for p in scanner.files_in(folder)], now)
    subs = [(name, _sheets(search._walk([folder / name]), now)) for name in scanner.subfolders_of(folder)]
    copies = len(scanner.find_duplicates([p for p, _ in items]))
    return summary_result(search._show(folder, base), items, subs, copies)


def summary_result(place: str, items: list, subs: list, copies: int) -> dict:
    """The folder_summary result for one folder: [(path, sheet)] in it, [(name, [(path, sheet)])] per subfolder."""
    return {"folder": place, "files": len(items),
            "kinds": {TYPE_NAMES.get(k, k): n for k, n in Counter(s["type"] for _, s in items).most_common()},
            "total_size": _mb(items), "exact_copies": copies,
            "subfolders": [{"folder": name, "files": len(inside), "size": _mb(inside)} for name, inside in subs]}


def overview_result(groups: list) -> dict:
    """The folder_summary result without a folder: [(main folder name, [(path, sheet)] with its subfolders)]."""
    rows = [{"folder": name, "files": len(items), "size": _mb(items)} for name, items in groups]
    return {"main_folders": rows, "total_files": sum(r["files"] for r in rows),
            "total_size": _mb([x for _, items in groups for x in items]), "note": "counts include subfolders"}
