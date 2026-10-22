"""The conversation loop: request -> inspect_folder -> explanation + proposed changes.

Nothing on disk changes here. The caller shows the preview and calls executor.apply
only after the user confirms.
"""

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import executor, llm, safety, scanner
from .schema import SYSTEM_PROMPT, TOOLS, tool_result

ASK_FOLDER = "Which folder do you mean? You can type its name or path, for example 'Downloads' or 'Desktop/new folder'."
GO_AHEAD = ("Yes, go ahead and call propose_changes now, with a short reason for every file. "
            "I will see a preview and confirm before anything changes.")
BAD_FORMAT = ("Error: 'changes' could not be read. It must be a JSON list of objects, for example "
              '[{"file": "scan_0031.pdf", "action": "move", "to_folder": "Bills", "reason": "phone bill"}]. '
              "Call propose_changes again with valid JSON.")
NO_PLAN = "(I couldn't put together a usable plan this time. Please ask again.)"
RETRIES = 2  # extra model calls when the model asks first or sends a plan that can't be read
ASKING = re.compile(r"\?|\b(would you like|shall i|should i|do you want|please confirm|let me know|"
                    r"haven'?t specified|not sure which)\b", re.I)


@dataclass
class Result:
    kind: str                       # "proposal", "explain", "ask", "refuse", "error"
    text: str = ""
    folder: Path = None
    steps: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    seconds: list = field(default_factory=list)


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


def run(user_text: str, base: Path = Path.home(), model: str = None) -> Result:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_text}]
    seconds = []

    t = time.perf_counter()
    first = llm.chat(messages, model=model, tools=TOOLS).message
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
    if safety.is_protected(folder):
        return Result("refuse", f"{folder} is a protected folder, so I won't change it.", seconds=seconds)
    if not safety.names_folder(folder_name, user_text):
        return Result("ask", ASK_FOLDER, seconds=seconds)  # the model picked a folder the user never named
    if not folder.is_dir():
        return Result("ask", f"I couldn't find the folder '{folder_name}'. Which folder do you mean?",
                      seconds=seconds)
    facts = scanner.scan(folder, folder_name or folder.name)

    messages.append(first)
    messages.append({"role": "tool", "tool_name": "inspect_folder",
                     "content": tool_result(facts)})
    text, changes, secs = decide(messages, user_text, model)
    seconds += secs
    if changes is None and safety.wants_changes(user_text):
        return Result("explain", f"{text}\n\n{NO_PLAN}".strip(), folder=folder, seconds=seconds)
    if changes is None or safety.NO_CHANGE.search(user_text):
        return Result("explain", text, folder=folder, seconds=seconds)
    changes = safety.clean_changes(changes, folder_name)
    allowed, blocked = safety.check_changes(changes, facts["files"], user_text)
    return Result("proposal", text, folder=folder, steps=executor.plan_steps(folder, allowed),
                  blocked=blocked, seconds=seconds)
