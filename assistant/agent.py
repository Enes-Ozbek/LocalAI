"""The conversation loop: request -> inspect_folder -> explanation + proposed changes.

Nothing on disk changes here. The caller shows the preview and calls executor.apply
only after the user confirms.

Big folders: the model sees at most scanner.MAX_FILES files per request, so a folder with more goes to it in
parts, each looking like a normal request. When the request names a kind of file ('all .tmp files'), only those
files go.

Questions about files ('how many files are there?', 'what's the biggest file in downloads', 'find my CV') go to
the model with read-only tools instead (schema.READ_TOOLS): it decides what to look at, code counts, sorts and
searches exactly, and the model answers from the result. Session remembers the conversation between requests.
"""

import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import answers, executor, filters, llm, safety, scanner, search
from .schema import READ_TOOLS, SYSTEM_PROMPT, TOOLS, question_prompt, tool_result

ASK_FOLDER = "Which folder do you mean? You can type its name or path, for example 'Downloads' or 'Desktop/new folder'."
GO_AHEAD = ("Yes, go ahead and call propose_changes now, with a short reason for every file. "
            "I will see a preview and confirm before anything changes.")
BAD_FORMAT = ("Error: 'changes' could not be read. It must be a JSON list of objects, for example "
              '[{"file": "scan_0031.pdf", "action": "move", "to_folder": "Bills", "reason": "phone bill"}]. '
              "Call propose_changes again with valid JSON.")
LOOK_FIRST = "Error: you haven't seen the folder yet. Call inspect_folder first, with the folder the user named."
ONLY_THESE = ("Error: the user only asked about {}. Leave the other files alone and call propose_changes again "
              "with the change the user asked for.")
NO_PLAN = "(I couldn't put together a usable plan this time. Please ask again.)"
RETRIES = 2  # extra model calls when the model asks first or sends a plan that can't be read
MAX_PARTS = 10  # at most 400 files per request, about 10 minutes on a 6 GB card
DELETES = re.compile(r"\b(delete|remove|trash|erase|get rid of)\b", re.I)
ASKING = re.compile(r"\?|\b(would you like|shall i|should i|do you want|please confirm|let me know|"
                    r"haven'?t specified|not sure which)\b", re.I)
MAX_TOOL_STEPS = 4  # tool calls per question before the model must answer
HISTORY_TURNS = 3   # earlier requests and answers the model sees with a question
HISTORY_CHARS = 600  # of each earlier answer
# model: the model picks the read-only tools (code answers only when it doesn't); code: answers.py and search.py
QUESTION_MODE = os.environ.get("QUESTION_MODE", "model")
LOOKUP = re.compile(r"\b(?:is|are)\s+there\b|\bdo\s+i\s+have\b|\bwhich\s+(?:file|folder|one)s?\b|"
                    r"\blist\b|\bwhat\s+(?:kinds?|types?)\s+of\s+files\b", re.I)
REFERS_BACK = re.compile(r"\b(there|here|in\s+it|this\s+folder|that\s+folder|the\s+same\s+folder)\b|"
                         r"\b(?:organi[sz]e|sort|clean|tidy|arrange|declutter)\s+it\b", re.I)
FOLLOW_UP = re.compile(r"^(?:(?:and|what|how)\s+about|and|also|i\s+meant?|it'?s|it\s+is|the\s+one\s+in|in|on|"
                       r"inside|from|my|the)\b\s*", re.I)


@dataclass
class Result:
    kind: str                       # "proposal", "explain", "ask", "refuse", "error", "search"
    text: str = ""
    folder: Path = None
    steps: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    seconds: list = field(default_factory=list)
    matches: list = field(default_factory=list)  # search results
    tools: list = field(default_factory=list)    # the read-only tools the model called for a question
    by_code: bool = False                         # a question answered by code, without the model


def _calls(message) -> list:
    return [(c.function.name, dict(c.function.arguments)) for c in (message.tool_calls or [])]


def _changes(message):
    """The proposed changes as a list, or None when the model did not call propose_changes
    or sent changes that can't be read (the 4B sometimes sends them as a broken JSON string)."""
    raw = next((a.get("changes", []) for n, a in _calls(message) if n == "propose_changes"), None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw if isinstance(raw, list) else None


def _explanation(text: str) -> str:
    """The part of a reply before the model starts asking ('Shall I organize them?', 'Please confirm...').
    Used when a retry produced the changes, so the text never contradicts the preview."""
    kept = []
    for line in (text or "").splitlines():
        if ASKING.search(line):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def decide(messages: list, user_text: str, model: str = None) -> tuple:
    """Step 2: the model explains the files and proposes changes. Returns (text, changes or None, seconds).
    If it only asks "shall I?" although the user asked for changes, it is told to go ahead; if its plan
    can't be read, it is told the right format. At most RETRIES extra calls."""
    history, seconds = list(messages), []
    for _ in range(1 + RETRIES):
        t = time.perf_counter()
        reply = llm.chat(history, model=model, tools=TOOLS).message
        seconds.append(round(time.perf_counter() - t, 2))
        if len(seconds) == 1:
            first_text = reply.content or ""
        changes = _changes(reply)
        if changes is not None or not safety.wants_changes(user_text):
            break
        history.append(reply)
        if any(n == "propose_changes" for n, _ in _calls(reply)):
            history.append({"role": "tool", "tool_name": "propose_changes", "content": BAD_FORMAT})
        else:
            history.append({"role": "user", "content": GO_AHEAD})
    text = first_text
    if changes is not None and len(seconds) > 1:
        text = "\n".join(x for x in (_explanation(first_text), _explanation(reply.content)) if x)
    return text, changes, seconds


def _fill_in_file(user_text: str, folder: Path, names: list, display: str) -> tuple:
    """('delete thesis.docx in downloads', 'The newest file in Downloads is thesis.docx.') when the request
    wants one file changed and picks it by position ('delete the last file' = the newest one);
    (user_text, '') otherwise."""
    found = safety.wants_changes(user_text) and safety.file_by_position(user_text, folder, names)
    if not found:
        return user_text, ""
    name, which = found
    return safety.fill_in(user_text, name), f"The {which} file in {display} is {name}."


def _question(user_text: str, base: Path, model: str, progress, last_folder: Path, history) -> Result:
    """The model answers a question with the read-only tools (run by answers.run_tool), up to MAX_TOOL_STEPS calls. Code does the counting,
    sorting and searching; the files of the last list or search are shown as a table. When the model answers
    without looking, or doesn't finish, the code answer (answers.py, search.py) is used instead."""
    system = question_prompt(search._show(last_folder, base) if last_folder is not None else None)
    messages = [{"role": "system", "content": system}] + list(history) + [{"role": "user", "content": user_text}]
    seconds, matches, folder, looked, said, used = [], [], None, False, "", []
    for _ in range(MAX_TOOL_STEPS + 1):
        t = time.perf_counter()
        reply = llm.chat(messages, model=model, tools=READ_TOOLS).message
        seconds.append(round(time.perf_counter() - t, 2))
        calls, said = _calls(reply), (reply.content or "").strip()
        if not calls:
            if looked and said:
                return Result("search" if matches else "explain", said, folder=folder, matches=matches,
                              seconds=seconds, tools=used)
            break
        name, args = calls[0]  # one at a time, so every result answers the call before it
        used.append({"tool": name, **args})
        result, shown, where = answers.run_tool(name, args, base, progress)
        looked = looked or "error" not in result
        matches = shown or matches
        folder = where or folder
        messages += [{"role": "assistant", "content": reply.content or "",
                      "tool_calls": [{"function": {"name": name, "arguments": args}}]},
                     {"role": "tool", "tool_name": name, "content": json.dumps(result, ensure_ascii=False)}]
    r = _code_answer(user_text, base, last_folder, progress)
    if r:
        r.seconds, r.tools = seconds + r.seconds, used
        return r
    return Result("explain", "I couldn't check that. Try naming the folder, for example "
                             "'how many files are in Downloads?', or ask me to find something.", seconds=seconds)


def _code_answer(user_text: str, base: Path, last_folder: Path, progress):
    """The answer without the model, or None when it isn't a question code can answer."""
    if answers.wants_answer(user_text):
        a = answers.answer(user_text, base, last_folder)
        return Result(a["kind"], a["text"], folder=a["folder"], matches=a["matches"], seconds=a["seconds"],
                      by_code=True)
    if search.wants_search(user_text):
        found = search.find(user_text, base, progress)
        return Result("search", found["text"], folder=found["scope"], matches=found["matches"],
                      seconds=found["seconds"], by_code=True)
    return None


class Session:
    """One conversation in the app or the command line. Each request still goes to run(), but the session
    remembers what it needs from the messages before: the folder of the last answer ('organize it',
    'how many files are there now?'), a request still waiting for its folder ('organize my files' ->
    'Which folder do you mean?' -> 'downloads'), the last question ('and desktop?') and the last few messages,
    which the model sees when it answers a question. Requests that change files reach the model alone, as in
    its training, with the folder written into them."""

    def __init__(self, base: Path, model: str = None):
        self.base, self.model = base, model
        self.folder = None    # the folder the last answer was about
        self.waiting = None   # a request that got 'Which folder do you mean?'
        self.question = None  # the last question
        self.history = []

    def run(self, user_text: str, progress=None) -> Result:
        text, name = user_text, self._folder_only(user_text)
        if name and self.waiting:
            text = f"{self.waiting.rstrip(' ?.!')} in {name}"
        elif name and self.question:
            text = self._swap_folder(self.question, name)
        elif self.folder is not None and not is_question(text) and REFERS_BACK.search(text) \
                and not answers.named_folder(text, self.base):
            text = f"{text.rstrip(' ?.!')} in {self._name(self.folder)}"
        r = run(text, self.base, self.model, progress, last_folder=self.folder,
                history=self.history[-2 * HISTORY_TURNS:])
        self.waiting = text if r.kind == "ask" else None
        self.question = text if is_question(text) and r.kind in ("explain", "search") else None
        if r.kind not in ("ask", "refuse", "error"):
            self.folder = r.folder
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": r.text[:HISTORY_CHARS]}]
        return r

    def _folder_only(self, user_text: str):
        """'downloads', 'my desktop', 'and in archive?', 'what about Desktop/new folder' -> the folder name as
        typed; None for anything else."""
        text = user_text.strip().strip("?.!, ")
        while (m := FOLLOW_UP.match(text)) and m.group(0):
            text = text[m.end():]
        text = re.sub(r"\s+folder$", "", text, flags=re.I).strip("?.!,\"' ")
        if not text or len(text.split()) > 4 or is_question(text) or safety.wants_changes(text):
            return None
        folder = answers._tool_folder(text, self.base)
        if folder is None or safety.is_protected(folder) or folder.resolve() == self.base.resolve():
            return None
        return text

    def _swap_folder(self, question: str, name: str) -> str:
        """'how many files are in downloads' + 'and desktop?' -> 'how many files are in desktop'."""
        old = answers.named_folder(question, self.base)
        if old is not None:
            for said in sorted({self._name(old), old.name}, key=len, reverse=True):
                swapped, n = re.subn(re.escape(said), name, question, count=1, flags=re.I)
                if n:
                    return swapped
        return f"{question.rstrip(' ?.!')} in {name}"

    def _name(self, folder: Path) -> str:
        """How to write the folder in a request: 'Downloads/New folder1', 'Desktop', or its full path."""
        try:
            return folder.relative_to(self.base).as_posix()
        except ValueError:
            label = answers._label(folder, self.base)
            return label if label in scanner.known_folders() else str(folder)


def _other_folder(to_folder: str, folder: Path, base: Path, user_text: str):
    """'move CV.docx from downloads to documents' means the user's Documents folder, not a new Downloads\\Documents.
    Returns that folder's full path (plus any subfolders the model named) when the request moves some files
    rather than organizing the whole folder, names the target, the target exists under base and the folder
    being organized has no subfolder of that name. Otherwise None: the target stays a subfolder, as before."""
    parts = [p for p in to_folder.replace("\\", "/").split("/") if p not in ("", ".", "..", "~")]
    if not parts or (folder / parts[0]).is_dir() or safety.whole_folder(user_text) \
            or not safety.names_folder(parts[0], user_text):
        return None
    top = scanner.resolve_folder(parts[0], base)
    if not top.is_dir() or top == folder or safety.is_protected(top) or safety.names_system_folder(parts[0]):
        return None
    return top.joinpath(*parts[1:])


def is_question(user_text: str) -> bool:
    """True for questions the read-only tools answer: 'how many files are there?', 'the newest file in archive',
    'find my CV', 'is there a pdf about taxes?'; not for requests that change files."""
    return answers.wants_answer(user_text) or search.wants_search(user_text) or \
        (bool(LOOKUP.search(user_text)) and not safety.wants_changes(user_text))


def run(user_text: str, base: Path = Path.home(), model: str = None, progress=None, last_folder: Path = None,
        history: list = ()) -> Result:
    """progress(text), if given, is told which part of a big folder the model is on. last_folder and history:
    the folder and the messages before this one (Session), for questions."""
    if is_question(user_text):
        if QUESTION_MODE == "code":
            r = _code_answer(user_text, base, last_folder, progress)
            if r:
                return r
        else:
            return _question(user_text, base, model, progress, last_folder, history)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_text}]
    seconds = []

    t = time.perf_counter()
    first = llm.chat(messages, model=model, tools=TOOLS).message
    seconds.append(round(time.perf_counter() - t, 2))
    calls = _calls(first)
    if calls and calls[0][0] == "propose_changes":
        # r8 sometimes plans before looking when the request names the file: it is told once to look first,
        # and only the new reply is kept, so the conversation looks like the ones it was trained on
        t = time.perf_counter()
        retry = messages + [first, {"role": "tool", "tool_name": "propose_changes", "content": LOOK_FIRST}]
        first = llm.chat(retry, model=model, tools=TOOLS).message
        seconds.append(round(time.perf_counter() - t, 2))
        calls = _calls(first)
    if not calls:
        return Result("explain", first.content or "", seconds=seconds)
    name, args = calls[0]
    if name == "ask_clarification":
        return Result("ask", args.get("question", ""), seconds=seconds)
    if name == "refuse":
        return Result("refuse", args.get("reason", ""), seconds=seconds)
    if name != "inspect_folder":
        return Result("error", f"The model called {name} before looking at the folder.", seconds=seconds)

    folder_name = args.get("folder", "")
    folder = scanner.resolve_folder(folder_name, base)
    if safety.is_protected(folder) or safety.names_system_folder(folder_name):
        return Result("refuse", f"{folder_name or folder} is a protected folder, so I won't change it.", seconds=seconds)
    if not safety.names_folder(folder_name, user_text):
        return Result("ask", ASK_FOLDER, seconds=seconds)  # the model picked a folder the user never named
    if not folder.is_dir():
        return Result("ask", f"I couldn't find the folder '{folder_name}'. Which folder do you mean?",
                      seconds=seconds)
    display = folder_name or folder.name
    paths = scanner.files_in(folder)
    duplicate_of = scanner.find_duplicates(paths)
    # 'delete the last file': from here on the model and the rules see the file's name instead
    user_text, lead = _fill_in_file(user_text, folder, [p.name for p in paths], display)
    messages[1] = {"role": "user", "content": user_text}
    messages.append(first)

    parts, picked, note = _parts(user_text, folder, display, paths, duplicate_of, base)
    if not parts:
        return Result("explain", note, folder=folder, seconds=seconds)
    lead = "\n\n".join(x for x in (lead, note) if x)
    planned = scanner.subfolders_of(folder)  # grows with the folders earlier parts created, so later parts reuse them
    saids, changes, failed = [], [], []
    for k, part in enumerate(parts, 1):
        if progress and len(parts) > 1:
            progress(f"Looking at part {k} of {len(parts)} ({len(part)} files). Each part takes about a minute.")
        facts = scanner.scan(folder, display, files=part, duplicate_of=duplicate_of, subfolders=list(planned))
        said, got, secs = _plan_part(messages + [{"role": "tool", "tool_name": "inspect_folder",
                                                  "content": tool_result(facts)}],
                                     facts, folder, display, base, user_text, model)
        seconds += secs
        saids.append(said)
        if got is None:
            failed.append(k)
            continue
        changes += got
        for c in got:
            to = isinstance(c, dict) and c.get("action") == "move" and str(c.get("to_folder") or "")
            top = to and to.replace("\\", "/").strip("/ ").split("/")[0]
            if top and top.lower() not in {p.lower() for p in planned} \
                    and not _other_folder(to, folder, base, user_text):
                planned.append(top)
    text = _join(lead, saids)
    if len(failed) == len(parts) and safety.wants_changes(user_text):
        return Result("explain", f"{text}\n\n{NO_PLAN}".strip(), folder=folder, seconds=seconds)
    if len(failed) == len(parts) or safety.NO_CHANGE.search(user_text):
        return Result("explain", text, folder=folder, seconds=seconds)
    if failed:
        text += f"\n\n(I couldn't put together a usable plan for part {', '.join(map(str, failed))}. " \
                "Ask again after applying this one for those files.)"
    now = datetime.now()
    sheets = {"files": [scanner.fact_sheet(p, duplicate_of, now, text=False) for p in paths]}
    allowed, blocked = _check(changes, sheets, folder, folder_name, base, user_text, picked)
    return Result("proposal", text, folder=folder, steps=executor.plan_steps(folder, allowed),
                  blocked=blocked, seconds=seconds)


def _plan_part(messages: list, facts: dict, folder: Path, display: str, base: Path, user_text: str,
               model: str) -> tuple:
    """The model's explanation and changes for one part: (text, changes or None, seconds)."""
    said, changes, seconds = decide(messages, user_text, model)
    if changes is None or safety.NO_CHANGE.search(user_text):
        return said, changes, seconds
    allowed, blocked = _check(changes, facts, folder, display, base, user_text)
    if not allowed and any(b["blocked"].startswith(safety.ONLY_ASKED) for b in blocked):
        # r8 sometimes changes a look-alike ('IMG_2034 (1).jpg') instead of the file asked about: told once
        asked = ", ".join(sorted(safety.asked_files(user_text, [s["file"] for s in facts["files"]])))
        messages = messages + [
            {"role": "assistant", "content": said,
             "tool_calls": [{"function": {"name": "propose_changes", "arguments": {"changes": changes}}}]},
            {"role": "tool", "tool_name": "propose_changes", "content": ONLY_THESE.format(asked)}]
        again, retried, secs = decide(messages, user_text, model)
        seconds += secs
        if retried is not None:
            said, changes = again or said, retried
    return said, changes, seconds


def _join(lead: str, saids: list) -> str:
    body = saids[0] if len(saids) == 1 else \
        "\n\n".join(f"Part {k}: {s}" for k, s in enumerate(saids, 1) if s)
    return "\n\n".join(x for x in (lead, body) if x).strip()


def _parts(user_text: str, folder: Path, display: str, paths: list, duplicate_of: dict, base: Path) -> tuple:
    """The files the model sees, in parts of up to MAX_FILES: (parts, picked, note). A folder that fits in one
    part goes whole, as always. In a bigger one, a request that names files gets only those (and their copies),
    and a request that names a kind of file ('delete all .tmp files') only the files of that kind. picked: the
    files the user asked to delete by kind, which the 'changed in the last 14 days' rule then lets through,
    as it does for a file named in the request."""
    if len(paths) <= scanner.MAX_FILES:
        return [paths], set(), ""
    now = datetime.now()
    sheets = {p: scanner.fact_sheet(p, duplicate_of, now, text=False) for p in paths}
    total, picked = f"{display} has {len(paths)} files", set()
    asked = safety.asked_files(user_text, [p.name for p in paths])
    if asked and not safety.whole_folder(user_text):
        originals = {duplicate_of.get(p, p.name) for p in paths if p.name in asked}
        chosen = [p for p in paths if duplicate_of.get(p, p.name) in originals]
        note = ""
    else:
        wanted = filters.parse(user_text, _folder_words(folder, base), here=(display, folder.name))
        chosen = [p for p in paths if wanted.matches(p, sheets[p])] if not wanted.empty() else paths
        if not chosen:
            return [], set(), f"{total}, but none of them are {wanted.describe()}, so there is nothing to change."
        note = f"{total}." if wanted.empty() else f"{total}; {len(chosen)} of them are {wanted.describe()}."
        if wanted.picks_files() and DELETES.search(user_text):
            picked = {p.name for p in chosen}
    if not safety.wants_changes(user_text) or safety.NO_CHANGE.search(user_text):
        shown = chosen[:scanner.MAX_FILES]
        more = f" Here are the first {len(shown)} by name." if len(chosen) > len(shown) else ""
        return [shown], set(), f"{note} {_overview(chosen, sheets)}{more}".strip()
    n = scanner.MAX_FILES
    parts = [chosen[i:i + n] for i in range(0, len(chosen), n)]
    if len(parts) > MAX_PARTS:
        note += f" This plan covers the first {MAX_PARTS * n}; ask again after applying it for the rest."
    elif len(parts) > 1:
        note += f" I went through them in {len(parts)} parts of up to {n} files."
    return parts[:MAX_PARTS], picked, note.strip()


def _folder_words(folder: Path, base: Path) -> tuple:
    """Folder names a request may mention as places rather than kinds of file ('organize my pictures',
    'move the pdfs to documents')."""
    names = [folder.name] + scanner.subfolders_of(folder)
    try:
        names += list(folder.relative_to(base).parts) + scanner.subfolders_of(base)
    except ValueError:
        pass
    return tuple(names)


TYPE_NAMES = {"images": "photos", "other": "other files"}


def _overview(paths: list, sheets: dict) -> str:
    """'120 photos, 80 documents and 3 other files; 12 are exact copies. 340 MB in all.'"""
    kinds = Counter(sheets[p]["type"] for p in paths)
    listed = [f"{n} {TYPE_NAMES.get(k, k)}" for k, n in kinds.most_common()]
    text = listed[0] if len(listed) == 1 else ", ".join(listed[:-1]) + " and " + listed[-1]
    copies = sum("duplicate_of" in sheets[p] for p in paths)
    if copies:
        text += f"; {copies} {'is an exact copy' if copies == 1 else 'are exact copies'}"
    mb = sum(sheets[p]["size_kb"] for p in paths) / 1024
    return f"{text}. {mb:,.0f} MB in all." if mb >= 1 else f"{text}."


def _check(changes: list, facts: dict, folder: Path, folder_name: str, base: Path, user_text: str,
           picked: set = frozenset()) -> tuple:
    """The model's changes -> (allowed, blocked) after the safety rules. A move to another folder the user
    named ('to documents') gets its full path in to_path."""
    changes = safety.clean_changes(changes, folder_name)
    for c in changes:
        outside = c.get("action") == "move" and c.get("to_folder") and \
            _other_folder(c["to_folder"], folder, base, user_text)
        if outside:
            c["to_path"] = str(outside)
            c["to_folder"] = outside.name  # passes the subfolder check; to_path is where it really goes
    return safety.check_changes(changes, facts["files"], user_text, picked)
