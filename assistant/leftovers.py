"""Leftovers of uninstalled apps: folders in AppData and dot-folders in the home folder that no installed program
owns any more.

Same split as the rest of the app. Code gathers the facts, one sheet per app folder rather than per file (a home
folder with 764,000 files has only a few hundred app folders): size, files, days since anything changed, what
kinds of files, its biggest subfolders, the installed programs whose names come closest, and whether a running
program uses it. The model sorts each folder into leftover, cache or ask (schema.LEFTOVER_PROMPT). Code then
applies the hard rules (check), and nothing moves until the user ticks it in the preview. Removed folders go
to a quarantine folder (executor.apply_cleanup), not the Recycle Bin, so Undo always works, even for 20 GB.

    "find leftovers of apps I uninstalled"   every app folder
    "clean up after Ollama"                  only the folders that match the app's name
"""

import json
import os
import re
import stat
import subprocess
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from . import llm
from .schema import CLEANUP_TOOLS, LEFTOVER_PROMPT, cleanup_result

ROOTS = ("AppData/Local", "AppData/Roaming", "AppData/LocalLow")
# folders that hold one folder per app: their subfolders are looked at, never the folder itself
CONTAINERS = ("AppData/Local/Programs", ".cache", ".config")
# Windows' own folders and this app's: never looked at, never touched
NEVER = {"microsoft", "packages", "programs", "publishers", "temp", "tmp", "virtualstore", "connecteddevicesplatform",
         "isolatedstorage", "placeholdertilelogofolder", "comms", "peerdistrepub", "toastnotificationmanagercompat",
         "package cache", "history", "temporary internet files", "application data", "d3dscache", "crashdumps",
         "microsoft_corporation", "windows", "start menu", "low", ".local-assistant", ".ssh", ".gnupg", ".local",
         ".identityservice", "common", "programs/common"}
# folders that may hold sign-in keys: the model may say leftover, but the user always decides
KEYS = {".aws", ".azure", ".kube", ".docker", ".gcloud", ".config/gcloud", ".config/gh", "github cli"}
RECENT_DAYS = 14   # a leftover changed in the last 2 weeks is only removed when the user named its app
OWNED = 0.85       # an installed program this close to the folder's name owns it: a leftover verdict becomes ask
SHOWN_MATCH = 0.7  # closer than this, an installed program is listed in the folder's facts
PART = 25          # folders per model call: about 2,000 tokens of facts
MAX_WALK = 200_000  # files counted per folder before it is called big and the walk stops

PROGRAM_EXT = {".exe", ".dll", ".sys", ".msi", ".jar", ".node", ".pyd", ".so", ".dylib", ".bat", ".ps1"}
CACHE_EXT = {".log", ".tmp", ".etl", ".dmp", ".old", ".bak", ".blob", ".ldb", ".pak", ".crdownload", ".part"}
CACHE_DIRS = {"cache", "caches", "_cacache", "gpucache", "code cache", "shadercache", "grshadercache", "dawncache",
              "dawngraphitecache", "dawnwebgpucache", "logs", "log", "crashpad", "crashes", "crash reports", "temp", "tmp",
              "blob_storage", "service worker", "cachestorage", "deriveddatacache", "webcache", "htmlcache",
              "httpcache", "downloads", "updates", "update", "packages", "pending", "blobs", "models"}
SAVE_EXT = {".sav", ".save", ".sl2", ".es3", ".savegame"}
SAVE_DIRS = {"saves", "save", "savegames", "savegame", "savedgames", "save games", "remote"}
DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".pkt", ".psd",
           ".blend", ".kra", ".xcf", ".tex"}
MEDIA_EXT = {".jpg", ".jpeg", ".png", ".heic", ".mp4", ".mov", ".mkv", ".wav", ".mp3", ".flac"}
PHOTO_MIN_KB = 200  # app folders are full of small icons: only bigger pictures count as the user's
GENERIC = {"inc", "ltd", "llc", "gmbh", "corp", "corporation", "co", "company", "software", "the", "app", "apps", "exe",
           "launcher", "client", "desktop", "setup", "installer", "updater", "for", "windows", "win", "x64", "x86",
           "bit", "64", "32", "edition", "version", "v", "studio", "studios", "games", "game", "entertainment",
           "interactive", "technologies", "technology", "systems", "data", "local", "user", "and", "of", "by",
           "ide", "standalone", "portable", "tools", "tool"}

LEFTOVER_WORDS = re.compile(r"\bleft\s*-?\s*overs?\b|\b(?:left|leave|leaves)\s+behind\b|\bleft\s+by\b|\bremnants?\b|\bresidu(?:e|al)s?\b|"
                            r"\bclean\s*(?:up\s+)?after\b|\buninstall(?:ed)?\b.*\b(?:files?|folders?|traces?|data|"
                            r"remains?|junk|left)\b|\b(?:files?|folders?|traces?|junk)\b.*\buninstall(?:ed)?\b|"
                            r"\bapp\s*data\b", re.I)
EVERY_APP = re.compile(r"\b(?:apps?|programs?|software|games?|everything|all|anything|uninstalled|my|pc|computer|"
                       r"i|things|stuff|old|ones?|which|what|any|appdata|app\s*data|find|show|delete|remove|clean|clear|list|get|"
                       r"search|look|check|see|files?|folders?)\b", re.I)
NAMED = [re.compile(p, re.I) for p in (
    r"\b(?:after|of|from|by)\s+(?:the\s+)?(?:app\s+|program\s+)?([\w][\w .+&'-]{1,40}?)"
    r"(?:'s)?(?:\s+(?:app|application|program|software))?\s*(?:[?.!,]|$|\s+(?:on|in|from|that|which|i)\b)",
    r"\b(?:did|does|has)\s+([\w][\w .+&-]{1,40}?)\s+leave\b",
    r"\b(?:find|show|delete|remove|clean(?:\s*up)?|clear|list)\s+(?:the\s+|all\s+)?([\w][\w.+&-]{1,30}?)(?:'s)?\s+"
    r"(?:left\s*-?\s*overs?|leftover\s+files|remnants|traces)\b",
    r"^([\w][\w.+&-]{1,30}?)(?:'s)?\s+(?:left\s*-?\s*overs?|leftover\s+files|remnants|traces)\b",
    r"\buninstall(?:ed)?\s+([\w][\w .+&-]{1,40}?)(?:\s*[,.;!?]|\s+(?:and|but|so|now|yesterday|today|last)\b|$)")]


@dataclass
class Program:
    name: str
    publisher: str = ""
    location: str = ""


def wants_leftovers(user_text: str) -> bool:
    """'find leftovers of apps I uninstalled', 'clean up after Ollama', 'what did docker leave behind?'"""
    return bool(LEFTOVER_WORDS.search(user_text))


def named_app(user_text: str) -> str:
    """'clean up after Ollama' -> 'Ollama'; '' when the request is about every app ('apps I uninstalled')."""
    for pattern in NAMED:
        m = pattern.search(user_text.strip())
        if m:
            name = re.sub(r"^(?:the|my)\s+", "", m.group(1).strip(" '\"")).strip()
            name = re.sub(r"\s+(?:left\s*-?\s*overs?|leftover\s+files|files|folders?)$", "", name, flags=re.I)
            if name and not EVERY_APP.search(name) and len(_compact(name)) >= 3:
                return name
    return ""


# ---- what is installed

def installed_programs(base: Path) -> tuple:
    """([Program], [paths of running programs]). Windows: the uninstall keys of the registry, Program Files,
    AppData/Local/Programs, Start Menu shortcuts and Store apps; running programs from Get-Process.
    A sandbox (or a test) describes its own PC in installed.json next to its AppData instead."""
    fake = base / "installed.json"
    if fake.is_file():
        data = json.loads(fake.read_text(encoding="utf-8"))
        programs = [Program(p["name"], p.get("publisher", ""), str(base / p["location"]) if p.get("location") else "")
                    for p in data.get("programs", [])]
        return programs, [str(base / r) for r in data.get("running", [])]
    if os.name != "nt":
        return [], []
    programs = _registry() + _program_folders(base) + _shortcuts(base) + _store_apps(base)
    return programs, _running()


def _registry() -> list:
    import winreg
    out = []
    keys = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, path in keys:
        try:
            root = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                        values = {}
                        for field in ("DisplayName", "Publisher", "InstallLocation", "DisplayIcon"):
                            try:
                                values[field] = str(winreg.QueryValueEx(k, field)[0] or "")
                            except OSError:
                                values[field] = ""
                except OSError:
                    continue
                if values["DisplayName"]:
                    where = values["InstallLocation"] or _folder_of(values["DisplayIcon"])
                    out.append(Program(values["DisplayName"], values["Publisher"], where))
    return out


def _folder_of(icon: str) -> str:
    """DisplayIcon is often 'C:\\...\\app.exe,0': its folder is where the program lives."""
    path = icon.split(",")[0].strip().strip('"')
    return str(Path(path).parent) if path.lower().endswith(".exe") else ""


def _program_folders(base: Path) -> list:
    out = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        root = os.environ.get(var)
        out += [Program(p.name, "", str(p)) for p in _subdirs(Path(root))] if root else []
    out += [Program(p.name, "", str(p)) for p in _subdirs(base / "AppData" / "Local" / "Programs")]
    return out


def _shortcuts(base: Path) -> list:
    roots = [base / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs"]
    if os.environ.get("ProgramData"):
        roots.append(Path(os.environ["ProgramData"]) / "Microsoft/Windows/Start Menu/Programs")
    out = []
    for root in roots:
        try:
            out += [Program(p.stem) for p in root.glob("*.lnk")] + [Program(p.stem) for p in root.glob("*/*.lnk")]
        except OSError:
            pass
    return out


def _store_apps(base: Path) -> list:
    """AppData/Local/Packages has a folder per Store app: 'SpotifyAB.SpotifyMusic_zpdnekdrzrea0' -> SpotifyMusic."""
    return [Program(p.name.split("_")[0].split(".")[-1], p.name.split(".")[0])
            for p in _subdirs(base / "AppData" / "Local" / "Packages")]


def _running() -> list:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-Process | Where-Object Path | Select-Object -ExpandProperty Path"],
                             capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return [line.strip() for line in out.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


# ---- matching folder names to programs

def _words(name: str) -> list:
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)  # 'EpicGamesLauncher' -> 'Epic Games Launcher'
    return [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in GENERIC and not re.fullmatch(r"\d+", w)]


def _compact(name: str) -> str:
    return "".join(_words(name))


def similarity(folder_name: str, program: str) -> float:
    """1.0 for the same name ('Docker' and 'Docker Desktop'), less for look-alikes, 0 for unrelated names."""
    a, b = _compact(folder_name), _compact(program)
    if len(a) < 3 or len(b) < 3:
        return 1.0 if a and a == b else 0.0
    if a == b:
        return 1.0
    if (a in b or b in a) and min(len(a), len(b)) >= 4:
        return 0.95
    wa, wb = set(_words(folder_name)), set(_words(program))
    shared = {w for w in wa & wb if len(w) >= 4}
    words = len(shared) / min(len(wa), len(wb)) if wa and wb else 0.0
    # spelling look-alikes only for longer names: 'pip' and '7zip' are not alike
    looks = SequenceMatcher(None, a, b).ratio() if min(len(a), len(b)) >= 5 else 0.0
    return round(max(looks, 0.9 * words), 2)


def closest(folder: Path, programs: list, n: int = 2) -> list:
    """The installed programs that come closest to the folder: [(Program, score)], best first. A program
    installed inside the folder (or the folder inside the program's) owns it outright."""
    scored = []
    here = os.path.normcase(str(folder))
    for p in programs:
        where = os.path.normcase(p.location.rstrip("\\/")) if p.location else ""
        if where and (where == here or where.startswith(here + os.sep) or here.startswith(where + os.sep)):
            score = 1.0
        else:
            score = max(similarity(folder.name, p.name), similarity(folder.name, p.publisher) * 0.95 if p.publisher else 0)
            if where:
                score = max(score, similarity(folder.name, Path(where).name))
        if score >= SHOWN_MATCH:
            scored.append((p, round(score, 2)))
    best = {}
    for p, s in sorted(scored, key=lambda x: -x[1]):
        best.setdefault(p.name.lower(), (p, s))
    return list(best.values())[:n]


# ---- the folders and their facts

def _subdirs(folder: Path) -> list:
    try:
        return sorted((p for p in folder.iterdir() if p.is_dir() and not _is_link(p)), key=lambda p: p.name.lower())
    except OSError:
        return []


def _is_link(path) -> bool:
    """Symlinks and junctions: AppData has some that point back up ('Application Data'), so walks skip them."""
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return True
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)  # REPARSE_POINT


def label(folder: Path, base: Path) -> str:
    """How the folder is shown and named to the model: 'AppData/Roaming/ollama app.exe', '.ollama'."""
    try:
        return folder.relative_to(base).as_posix()
    except ValueError:
        return str(folder)


def _never(rel: str) -> bool:
    low = rel.lower()
    name = low.rsplit("/", 1)[-1]
    return name in NEVER or low in NEVER or name.startswith("claude") or low.startswith(".claude")


def candidates(base: Path) -> list:
    """Every app folder: the subfolders of AppData Local, Roaming and LocalLow, the subfolders of folders that hold
    one per app (AppData/Local/Programs, .cache, .config), and dot-folders in the home folder."""
    out = []
    containers = {c.lower() for c in CONTAINERS}
    tops = [d for root in ROOTS for d in _subdirs(base / root)] + \
           [d for d in _subdirs(base) if d.name.startswith(".")]
    for d in tops:
        rel = label(d, base)
        if rel.lower() in containers:
            out += [c for c in _subdirs(d) if not _never(label(c, base))]
        elif not _never(rel):
            out.append(d)
    return out


def walk(folder: Path, now: float = None) -> dict:
    """Size, files, newest change and kinds of files under one folder, without following links."""
    now = now or time.time()
    total, files, newest, git = 0, 0, 0.0, False
    kinds = {"programs": 0, "cache_logs": 0, "saves": 0, "documents": 0, "photos_videos": 0, "settings_data": 0}
    sizes = {}  # immediate subfolder -> bytes
    stack = [(folder, None, False, False)]
    try:
        newest = folder.stat().st_mtime
    except OSError:
        pass
    while stack and files < MAX_WALK:
        d, top, in_cache, in_saves = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_symlink() or getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
                    continue
                if e.is_dir(follow_symlinks=False):
                    low = e.name.lower()
                    git = git or low == ".git"
                    stack.append((e.path, top or e.name, in_cache or low in CACHE_DIRS, in_saves or low in SAVE_DIRS))
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            files += 1
            total += st.st_size
            newest = max(newest, st.st_mtime)
            if top:
                sizes[top] = sizes.get(top, 0) + st.st_size
            ext = os.path.splitext(e.name)[1].lower()
            if ext in SAVE_EXT or in_saves:
                kinds["saves"] += 1
            elif ext in PROGRAM_EXT:
                kinds["programs"] += 1
            elif in_cache or ext in CACHE_EXT:
                kinds["cache_logs"] += 1
            elif ext in DOC_EXT:
                kinds["documents"] += 1
            elif ext in MEDIA_EXT and st.st_size >= PHOTO_MIN_KB * 1024:
                kinds["photos_videos"] += 1
            else:
                kinds["settings_data"] += 1
    biggest = sorted(sizes.items(), key=lambda kv: -kv[1])[:3]
    return {"bytes": total, "files": files, "more": files >= MAX_WALK, "days": int(max(0, now - newest) // 86400),
            "kinds": {k: v for k, v in kinds.items() if v}, "git": git,
            "biggest": [{"name": n, "mb": _mb(b)} for n, b in biggest if b]}


def _mb(n: int) -> float:
    mb = n / 1048576
    return round(mb, 1) if mb >= 0.1 else round(mb, 3)


def in_use(folder: Path, running: list) -> bool:
    here = os.path.normcase(str(folder)) + os.sep
    return any(os.path.normcase(r).startswith(here) for r in running)


def own_work(w: dict) -> bool:
    """Signs of the user's own files: game saves, documents, photos or videos, a git project."""
    k = w["kinds"]
    return w["git"] or k.get("saves", 0) > 0 or k.get("documents", 0) >= 3 or k.get("photos_videos", 0) >= 5


def sheet(folder: Path, base: Path, programs: list, running: list, now: float = None) -> dict:
    """One folder's facts, exactly as the model sees them (plus 'w', code's own copy, taken out before)."""
    w = walk(folder, now)
    out = {"folder": label(folder, base), "size_mb": _mb(w["bytes"]),
           "files": f"{w['files']}+" if w["more"] else w["files"], "last_change_days": w["days"],
           "kinds": w["kinds"]}
    if w["biggest"]:
        out["biggest_inside"] = [f"{b['name']} {b['mb']} MB" for b in w["biggest"]]
    if w["git"]:
        out["has_git_project"] = True
    out["closest_installed"] = [{"program": p.name, "score": s} for p, s in closest(folder, programs)]
    out["used_by_running_program"] = in_use(folder, running)
    return out | {"w": w}


def matches_app(folder: Path, app: str) -> bool:
    """'clean up after Ollama': '.ollama', 'Ollama' and 'ollama app.exe' match, 'Origin' doesn't."""
    a, f = _compact(app), _compact(folder.name)
    return bool(a) and bool(f) and (a in f or (len(f) >= 4 and f in a) or SequenceMatcher(None, a, f).ratio() >= 0.85)


def scan(base: Path, app: str = "", progress=None, now: float = None) -> tuple:
    """(sheets, programs). app: only the folders that match its name."""
    programs, running = installed_programs(base)
    folders = [d for d in candidates(base) if not app or matches_app(d, app)]
    sheets = []
    for k, d in enumerate(folders, 1):
        if progress and (k == 1 or k % 10 == 0):
            progress(f"Reading app folders: {k} of {len(folders)}.")
        sheets.append(sheet(d, base, programs, running, now))
    return sheets, programs


# ---- the model

GO_AHEAD = ("Call propose_cleanup now, with an entry for every leftover, cache or folder to ask about. "
            "The user sees a preview and confirms before anything changes.")
BAD_FORMAT = ("Error: 'items' could not be read. It must be a JSON list, for example "
              '[{"folder": "AppData/Local/Origin", "verdict": "leftover", "app": "Origin", '
              '"reason": "EA\'s old launcher, not installed"}]. Call propose_cleanup again.')
VERDICTS = ("leftover", "cache", "ask")


def model_sheets(sheets: list) -> list:
    return [{k: v for k, v in s.items() if k != "w"} for s in sheets]


def _items(message):
    """The proposed items as a list, or None when the model didn't call propose_cleanup or sent unreadable JSON."""
    raw = None
    for c in message.tool_calls or []:
        if c.function.name == "propose_cleanup":
            raw = dict(c.function.arguments).get("items", [])
            break
    else:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw if isinstance(raw, list) else None


def decide(user_text: str, part: list, app: str = "", model: str = None) -> tuple:
    """The model's items for one part of the folders: (items or None, seconds). One extra call when it answers
    without proposing, one when its list can't be read."""
    messages = [{"role": "system", "content": LEFTOVER_PROMPT}, {"role": "user", "content": user_text},
                {"role": "assistant", "content": "",
                 "tool_calls": [{"function": {"name": "inspect_app_folders", "arguments": {"app": app}}}]},
                {"role": "tool", "tool_name": "inspect_app_folders", "content": cleanup_result(model_sheets(part), app)}]
    seconds = []
    for _ in range(2):
        t = time.perf_counter()
        reply = llm.chat(messages, model=model, tools=CLEANUP_TOOLS).message
        seconds.append(round(time.perf_counter() - t, 2))
        items = _items(reply)
        if items is not None:
            return items, seconds
        messages.append(reply)
        if any(c.function.name == "propose_cleanup" for c in reply.tool_calls or []):
            messages.append({"role": "tool", "tool_name": "propose_cleanup", "content": BAD_FORMAT})
        else:
            messages.append({"role": "user", "content": GO_AHEAD})
    return None, seconds


# ---- the hard rules

def check(items: list, sheets: list, app: str = "") -> tuple:
    """The model's items -> (steps, blocked). A step is ticked in the preview unless the user should look first
    (ask, own work, sign-in keys, an installed program that owns it). Blocked: what the rules never allow.
    Folders the model left out are kept."""
    by_label = {s["folder"]: s for s in sheets}
    by_name = {}
    for s in sheets:
        by_name.setdefault(s["folder"].rsplit("/", 1)[-1].lower(), []).append(s)
    steps, blocked, seen = [], [], set()
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("folder") or "").replace("\\", "/").strip("/ ")
        s = by_label.get(name)
        if s is None:  # the model wrote only the last part ('Origin'): fine when that names one folder
            same = by_name.get(name.rsplit("/", 1)[-1].lower(), [])
            s = same[0] if len(same) == 1 else None
        verdict = str(it.get("verdict") or "").lower()
        reason = str(it.get("reason") or "").strip() or "no reason given"
        what = str(it.get("app") or "").strip()
        if s is None:
            blocked.append({"folder": name, "verdict": verdict, "blocked": "no such app folder in the scan"})
            continue
        if s["folder"] in seen or verdict not in VERDICTS:
            continue
        seen.add(s["folder"])
        problem, look = _rule(s, verdict, app, what)
        if problem:
            blocked.append({"folder": s["folder"], "verdict": verdict, "app": what, "reason": reason, "blocked": problem})
            continue
        steps.append({"op": "quarantine", "src": "", "folder": s["folder"], "contents_only": verdict == "cache",
                      "verdict": verdict, "app": what, "reason": reason, "size_kb": round(s["w"]["bytes"] / 1024),
                      "last_change_days": s["w"]["days"], "checked": verdict != "ask" and not look,
                      "look": look})
    return steps, blocked


def _rule(s: dict, verdict: str, app: str, what: str = "") -> tuple:
    """(why it is blocked, or ''; why the user should look first, or '')."""
    w, rel = s["w"], s["folder"]
    if _never(rel):
        return "Windows or this app uses it", ""
    if s["used_by_running_program"]:
        return "a running program uses it", ""
    named = bool(app) and matches_app(Path(rel), app)
    if verdict == "leftover" and w["days"] < RECENT_DAYS and not named:
        return (f"changed {w['days']} day{'' if w['days'] == 1 else 's'} ago; if its app is gone, ask by name, "
                f"for example 'clean up after {what or Path(rel).name}'"), ""
    if verdict == "cache" and not w["kinds"].get("cache_logs"):
        return "the scan found no cache or log files in it", ""
    look = ""
    owner = next((m for m in s["closest_installed"] if m["score"] >= OWNED), None)
    if verdict == "leftover" and owner:
        look = f"{owner['program']} is installed and may own it"
    elif own_work(w):
        look = "it may hold your own files (saves, documents, photos or a project)"
    elif rel.lower() in KEYS or Path(rel).name.lower() in KEYS:
        look = "it may hold sign-in keys"
    return "", look


def plan(user_text: str, base: Path, model: str = None, progress=None) -> dict:
    """The whole job: scan, the model in parts of PART folders, the rules. Nothing on disk changes here."""
    app = named_app(user_text)
    sheets, programs = scan(base, app, progress)
    if not sheets:
        where = f"for {app} " if app else ""
        return {"text": f"I found no app folders {where}in AppData or in dot-folders of your home folder.",
                "steps": [], "blocked": [], "seconds": [], "looked": 0, "app": app}
    parts = [sheets[i:i + PART] for i in range(0, len(sheets), PART)]
    items, seconds, failed = [], [], 0
    for k, part in enumerate(parts, 1):
        if progress:
            progress(f"Asking the model about part {k} of {len(parts)} ({len(part)} app folders).")
        got, secs = decide(user_text, part, app, model)
        seconds += secs
        if got is None:
            failed += 1
        else:
            items += got
    steps, blocked = check(items, sheets, app)
    for s in steps:
        s["src"] = str(base / s["folder"])
    return {"text": summary(sheets, steps, blocked, app, len(parts), failed), "steps": steps, "blocked": blocked,
            "seconds": seconds, "looked": len(sheets), "app": app}


def summary(sheets: list, steps: list, blocked: list, app: str, parts: int, failed: int) -> str:
    total = sum(s["w"]["bytes"] for s in sheets) / 1048576
    what = f"{len(sheets)} app folder{'s' if len(sheets) != 1 else ''}" + (f" of {app}" if app else "")
    text = f"I looked at {what} ({_size(total)})" + (f" in {parts} parts." if parts > 1 else ".")
    groups = [([x for x in steps if x["checked"] and x["verdict"] == "leftover"], "leftover", "leftovers"),
              ([x for x in steps if x["checked"] and x["verdict"] == "cache"], "cache", "caches"),
              ([x for x in steps if not x["checked"]], "folder to check yourself", "folders to check yourself")]
    counts = [f"{len(g)} {one if len(g) == 1 else many} ({_size(sum(x['size_kb'] for x in g) / 1024)})"
              for g, one, many in groups if g]
    if counts:
        text += " The model found " + ", ".join(counts) + "."
    else:
        text += " The model found nothing to clean up."
    if blocked:
        text += f" {len(blocked)} more {'was' if len(blocked) == 1 else 'were'} held back by the safety rules."
    if failed:
        text += f" (No usable answer for {failed} of the {parts} parts. Ask again for those.)"
    kept = len(sheets) - len(steps) - len(blocked)
    if kept > 0:
        text += f" The other {kept} stay: the model said they belong to installed programs."
    return text


def _size(mb: float) -> str:
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB" if mb >= 1 else "under 1 MB"
