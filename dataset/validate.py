"""Checks every record in a dataset file before it is used for training.

    python dataset/validate.py dataset/seed_examples.jsonl

A record with any ERROR must be fixed or dropped. The checks are the same safety
rules the app enforces (assistant/safety.py), so the model is never trained on an
answer the app would block.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant.safety import (NO_CHANGE, TEMP_ENDINGS, asked_files, check_changes, copy_groups,  # noqa: E402
                              names_folder)
from assistant.schema import TOOL_NAMES  # noqa: E402

# Training answers may delete only what the teacher prompt allows. The app lets the model decide,
# but the first fine-tune copied the teacher's deletes of old document versions, archives and photos.


def deletable(sheet: dict, has_copy: set) -> bool:
    """Exact duplicates, installers, temporary or broken downloads and empty files."""
    return (sheet["file"] in has_copy or sheet.get("type") == "installers"
            or sheet["file"].lower().endswith(TEMP_ENDINGS) or sheet.get("size_kb", 1) < 1)


def bad_changes(changes: list, sheets: list, user: str) -> list:
    """[(change, why)] for every proposed change that breaks a rule. A file the user named may be deleted
    even if it isn't a duplicate, installer or temporary file: the user asked for it."""
    allowed, blocked = check_changes(changes, sheets, user)
    by_name = {s["file"]: s for s in sheets}
    has_copy = {f for files in copy_groups(sheets).values() if len(files) > 1 for f in files}
    asked = asked_files(user, list(by_name))
    return [(b, b["blocked"]) for b in blocked] + \
        [(c, "deleted, but it is not a duplicate, installer or temporary file") for c in allowed
         if c["action"] == "delete" and not deletable(by_name[c["file"]], has_copy) and c["file"] not in asked]


def check(rec: dict) -> list:
    errors = []
    msgs = rec.get("messages", [])
    if len(msgs) < 3 or msgs[0]["role"] != "system" or msgs[1]["role"] != "user":
        return ["must start with a system and a user message"]
    user = msgs[1]["content"]
    facts, proposed = None, False

    for i, m in enumerate(msgs[2:], start=2):
        if m["role"] == "tool":
            prev = msgs[i - 1]
            if prev["role"] != "assistant" or not prev.get("tool_calls"):
                errors.append(f"message {i}: tool result without a tool call before it")
            facts = json.loads(m["content"])
            if not isinstance(facts, dict) or "files" not in facts:
                errors.append(f"message {i}: tool result must be {{folder, subfolders, files}}")
                facts = None
            continue
        if m["role"] != "assistant":
            errors.append(f"message {i}: unexpected role {m['role']}")
            continue
        for tc in m.get("tool_calls", []):
            name, args = tc["function"]["name"], tc["function"]["arguments"]
            if name not in TOOL_NAMES:
                errors.append(f"unknown tool {name}")
            elif name == "inspect_folder" and not args.get("folder"):
                errors.append("inspect_folder without a folder")
            elif name == "inspect_folder" and not names_folder(args["folder"], user):
                errors.append(f"inspect_folder on '{args['folder']}', which the request does not name")
            elif name == "propose_changes":
                proposed = True
                if facts is None:
                    errors.append("propose_changes before inspect_folder")
                    continue
                errors += [f"{c.get('file')}: {why}" for c, why in bad_changes(args.get("changes", []), facts["files"], user)]
                if not args.get("changes"):
                    errors.append("propose_changes with no changes (leave the call out instead)")
    last = msgs[-1]
    if last["role"] != "assistant":
        errors.append("must end with an assistant message")
    if proposed and NO_CHANGE.search(user):
        errors.append("proposed changes although the user asked for no changes")
    return errors


def drop_bad_changes(rec: dict):
    """A teacher draft that broke a rule for a few files, without those files' changes: one bad delete shouldn't
    throw away the 15 good decisions next to it. Returns (fixed record, dropped file names), or (None, names)
    when it can't be saved: nothing would be left, or the explanation talks about a dropped file."""
    msgs = rec.get("messages", [])
    tool = next((m for m in msgs if m["role"] == "tool"), None)
    final = msgs[-1] if msgs else {}
    call = next((tc for tc in final.get("tool_calls", []) if tc["function"]["name"] == "propose_changes"), None)
    if tool is None or call is None or len(msgs) < 2:
        return None, []
    sheets, user = json.loads(tool["content"])["files"], msgs[1]["content"]
    changes = call["function"]["arguments"].get("changes", [])
    if not isinstance(changes, list) or not all(isinstance(c, dict) for c in changes):
        return None, []
    bad = bad_changes(changes, sheets, user)
    dropped = sorted({c.get("file", "?") for c, _ in bad})
    keep = [c for c in changes if c.get("file") not in dropped]
    text = (final.get("content") or "").lower()
    named = any(n.lower() in text or (len(Path(n).stem) >= 5 and Path(n).stem.lower() in text) for n in dropped)
    if not keep or named:
        return None, dropped
    fixed = json.loads(json.dumps(rec))
    fixed["messages"][-1]["tool_calls"] = [
        {**tc, "function": {**tc["function"], "arguments": {**tc["function"]["arguments"], "changes": keep}}}
        if tc["function"]["name"] == "propose_changes" else tc for tc in final["tool_calls"]]
    fixed.pop("errors", None)
    fixed["meta"] = {**fixed.get("meta", {}), "dropped": dropped}
    return (fixed, dropped) if not check(fixed) else (None, dropped)


def main(path: str) -> int:
    bad = total = 0
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            total += 1
            errors = check(json.loads(line))
            for e in errors:
                print(f"record {n}: ERROR {e}")
            bad += bool(errors)
    print(f"{path}: {total} records, {bad} with errors")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent / "seed_examples.jsonl")))
