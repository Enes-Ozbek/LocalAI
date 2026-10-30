"""Reads which files a request is about: 'all .tmp files', 'the photos', 'pdfs older than a year',
'videos bigger than 500 MB', 'files with IMG in the name'.

Used to pick the files of a big folder that go to the model (the model still decides what happens to each),
and by search. Only narrows: a request without any of these words means every file.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .scanner import FILE_TYPES

KIND_WORDS = {
    "images": ("photo", "photos", "picture", "pictures", "image", "images", "pic", "pics", "selfies"),
    "videos": ("video", "videos", "movie", "movies", "clip", "clips"),
    "audio": ("music", "song", "songs", "audio", "recording", "recordings"),
    "documents": ("document", "documents", "doc", "docs"),
    "spreadsheets": ("spreadsheet", "spreadsheets", "excel"),
    "presentations": ("presentation", "presentations", "slide", "slides", "powerpoint", "powerpoints"),
    "archives": ("archive", "archives", "compressed"),
    "installers": ("installer", "installers", "setup", "setups"),
    "screenshots": ("screenshot", "screenshots"),
    "temporary": ("temp", "temporary", "partial", "unfinished", "incomplete"),
    "duplicates": ("duplicate", "duplicates", "duplicated", "copies"),
    "empty": ("empty",),
}
WORD_KIND = {w: k for k, words in KIND_WORDS.items() for w in words}
TEMP_ENDINGS = (".tmp", ".crdownload", ".part", ".partial")
# extensions people write without the dot ('delete the pdfs'); short or common words are left out
BARE_EXTS = {"pdf", "doc", "docx", "txt", "rtf", "odt", "xls", "xlsx", "csv", "ppt", "pptx", "jpg", "jpeg", "png",
             "gif", "bmp", "webp", "heic", "mp4", "mkv", "avi", "mov", "wmv", "mp3", "wav", "flac", "m4a", "zip",
             "rar", "7z", "exe", "msi", "iso", "tmp", "crdownload", "torrent", "log", "json", "html"}
NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "couple": 2, "few": 3}
UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
SIZE_KB = {"kb": 1, "mb": 1024, "gb": 1024 * 1024}
N = r"(\d+|" + "|".join(NUMBERS) + r")"
UNIT = r"(day|week|month|year)s?"
SIZE = r"(\d+(?:[.,]\d+)?)\s*(kb|mb|gb)\b"
QUOTED = r"(?<!\w)[\"'“‘]([^\"'“”‘’]{1,40})[\"'”’](?!\w)"  # not the ' in what's or don't
TARGET = re.compile(r"\b(?:to|into|in|inside|from|on|under)\s+(?:my\s+|the\s+|a\s+|an\s+)?(?:new\s+)?"
                    r"(?:folder|subfolder)?\s*(?:called|named)?\s*$", re.I)


@dataclass
class Filter:
    kinds: set = field(default_factory=set)
    exts: set = field(default_factory=set)
    name_has: list = field(default_factory=list)
    older_days: int = None    # changed more than this many days ago
    newer_days: int = None    # changed less than this many days ago
    year: int = None
    min_kb: float = None
    max_kb: float = None
    words: list = field(default_factory=list)  # what's left of the request: the search words

    def picks_files(self) -> bool:
        """True when the request names a kind of file or a name ('all .tmp files'), not only an age or size."""
        return bool(self.kinds or self.exts or self.name_has)

    def empty(self) -> bool:
        return not (self.picks_files() or self.older_days is not None or self.newer_days is not None
                    or self.year or self.min_kb is not None or self.max_kb is not None)

    def matches(self, path: Path, sheet: dict) -> bool:
        name, ext = path.name.lower(), path.suffix.lower()
        if self.kinds or self.exts:
            if not (ext in self.exts or any(_is_kind(k, name, ext, sheet) for k in self.kinds)):
                return False
        if any(n.lower() not in name for n in self.name_has):
            return False
        age = sheet["age_days"]
        if self.older_days is not None and age < self.older_days:
            return False
        if self.newer_days is not None and age >= self.newer_days:
            return False
        if self.year and not sheet["modified"].startswith(str(self.year)):
            return False
        if self.min_kb is not None and sheet["size_kb"] < self.min_kb:
            return False
        if self.max_kb is not None and sheet["size_kb"] > self.max_kb:
            return False
        return True

    def describe(self) -> str:
        """'.pdf and .tmp files older than 1 year' for the explanation."""
        what = " and ".join(sorted(KIND_NAMES.get(k, k) for k in self.kinds) + sorted(self.exts)) or "files"
        parts = [what + " files" if self.exts and not self.kinds else what]
        if self.name_has:
            parts.append("with " + " and ".join(f"'{n}'" for n in self.name_has) + " in the name")
        if self.older_days is not None:
            parts.append(f"older than {_days(self.older_days)}")
        if self.newer_days is not None:
            parts.append(f"from the last {_days(self.newer_days)}")
        if self.year:
            parts.append(f"from {self.year}")
        if self.min_kb is not None:
            parts.append(f"bigger than {_size(self.min_kb)}")
        if self.max_kb is not None:
            parts.append(f"smaller than {_size(self.max_kb)}")
        return " ".join(parts)


KIND_NAMES = {"images": "photos", "temporary": "temporary files", "duplicates": "duplicates",
              "empty": "empty files", "installers": "installers", "screenshots": "screenshots"}


def _is_kind(kind: str, name: str, ext: str, sheet: dict) -> bool:
    if kind == "screenshots":
        return "screenshot" in name or "screen shot" in name
    if kind == "temporary":
        return name.endswith(TEMP_ENDINGS)
    if kind == "duplicates":
        return "duplicate_of" in sheet
    if kind == "empty":
        return sheet["size_kb"] == 0
    return ext in FILE_TYPES.get(kind, ())


def _days(n: int) -> str:
    for unit, d in (("year", 365), ("month", 30), ("week", 7)):
        if n >= d and n % d == 0:
            return f"{n // d} {unit}{'s' if n // d > 1 else ''}"
    return f"{n} day{'s' if n != 1 else ''}"


def _size(kb: float) -> str:
    return f"{kb / 1024 / 1024:g} GB" if kb >= 1024 * 1024 else f"{kb / 1024:g} MB" if kb >= 1024 else f"{kb:g} KB"


def _num(word: str) -> int:
    return int(word) if word.isdigit() else NUMBERS[word]


def parse(text: str, folder_words: tuple = (), now: datetime = None, here: tuple = ()) -> Filter:
    """The file filter in a request. folder_words: names of folders on the PC, so 'move the pdfs to documents'
    or 'organize my pictures' aren't read as kinds of file. here: the folder being changed, whose name is never
    a kind of file ('organize archive')."""
    now = now or datetime.now()
    f = Filter()
    low = text.lower()
    # names in quotes or 'with X in the name', but not 'a folder called "Bills"'
    for m in re.finditer(QUOTED, text):
        if not re.search(r"\b(folder|subfolder|called|named)\s*$", text[:m.start()], re.I):
            f.name_has.append(m.group(1).strip())
        low = low[:m.start()] + " " * (m.end() - m.start()) + low[m.end():]
    for pat in (r"\bwith\s+(\S+)\s+in\s+(?:the|their|its)\s+names?\b", r"\b(?:start|starts|starting|begin|begins|beginning)\s+with\s+(\S+)",
                r"\bnames?\s+contain(?:s|ing)?\s+(\S+)"):
        for m in re.finditer(pat, low):
            f.name_has.append(m.group(1).strip(".,;:!?"))
            low = low.replace(m.group(0), " ")
    low = _take_dates(low, f, now)
    low = _take_sizes(low, f)
    for m in re.finditer(r"(?<![\w.])\.([a-z0-9]{1,10})\b", low):
        f.exts.add("." + m.group(1))
    low = re.sub(r"(?<![\w.])\.([a-z0-9]{1,10})\b", " ", low)
    folder_set = {w.lower() for name in folder_words for w in re.findall(r"\w+", name)}
    here_set = {w.lower() for name in here for w in re.findall(r"\w+", name)}
    words = []
    for m in re.finditer(r"[a-z0-9]+", low):
        w = m.group(0)
        before = low[:m.start()]
        is_folder = w in here_set or \
            (w in folder_set and (TARGET.search(before) or re.search(r"\b(my|the)\s*$", before)))
        if w in WORD_KIND and not is_folder:
            f.kinds.add(WORD_KIND[w])
        elif w in BARE_EXTS or (w.endswith("s") and w[:-1] in BARE_EXTS):
            f.exts.add("." + (w if w in BARE_EXTS else w[:-1]))
        else:
            words.append(w)
    if "duplicates" in f.kinds and "exact" in words:
        words.remove("exact")
    f.words = words
    return f


def _take_dates(low: str, f: Filter, now: datetime) -> str:
    rules = [
        (rf"\b(?:older than|more than|over)\s+{N}\s+{UNIT}(?:\s+old)?\b", lambda m: ("older", _num(m[1]) * UNIT_DAYS[m[2]])),
        (rf"\b{N}\s+{UNIT}\s+old\b", lambda m: ("older", _num(m[1]) * UNIT_DAYS[m[2]])),
        (rf"\b(?:in|within|from|during)\s+the\s+(?:last|past)\s+{N}\s+{UNIT}\b", lambda m: ("newer", _num(m[1]) * UNIT_DAYS[m[2]])),
        (rf"\b(?:in|within|from|during)\s+the\s+(?:last|past)\s+{UNIT}\b", lambda m: ("newer", UNIT_DAYS[m[1]])),
        (r"\btoday\b", lambda m: ("newer", 1)),
        (r"\byesterday\b", lambda m: ("newer", 2)),
        (r"\b(?:this|last|past)\s+week\b", lambda m: ("newer", 7 if "this" in m[0] else 14)),
        (r"\b(?:this|last|past)\s+month\b", lambda m: ("newer", 30 if "this" in m[0] else 60)),
        (r"\bthis\s+year\b", lambda m: ("year", now.year)),
        (r"\blast\s+year\b", lambda m: ("year", now.year - 1)),
        (r"\b(?:in|from|during|of)\s+((?:19|20)\d\d)\b", lambda m: ("year", int(m[1]))),
        (r"\b((?:19|20)\d\d)\b(?=\s+(?:photos|pictures|files|documents|pdfs|videos|screenshots)\b)", lambda m: ("year", int(m[1]))),
    ]
    for pat, read in rules:
        for m in list(re.finditer(pat, low)):
            what, value = read(m)
            if what == "older":
                f.older_days = value
            elif what == "newer":
                f.newer_days = value
            else:
                f.year = value
            low = low.replace(m.group(0), " ")
    return low


def _take_sizes(low: str, f: Filter) -> str:
    for m in list(re.finditer(rf"\b(?:bigger|larger|greater|more)\s+than\s+{SIZE}|\b(?:over|above)\s+{SIZE}", low)):
        num, unit = (m[1], m[2]) if m[1] else (m[3], m[4])
        f.min_kb = float(num.replace(",", ".")) * SIZE_KB[unit]
        low = low.replace(m.group(0), " ")
    for m in list(re.finditer(rf"\b(?:smaller|less)\s+than\s+{SIZE}|\b(?:under|below)\s+{SIZE}", low)):
        num, unit = (m[1], m[2]) if m[1] else (m[3], m[4])
        f.max_kb = float(num.replace(",", ".")) * SIZE_KB[unit]
        low = low.replace(m.group(0), " ")
    m = re.search(r"\b(big|large|huge|biggest|largest)\b(?=(?:\s+\w+)?\s+files?\b)", low)
    if m and f.min_kb is None:
        f.min_kb = 100 * 1024
        low = low.replace(m.group(0), " ", 1)
    return low
