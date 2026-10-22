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
from assistant.safety import NO_CHANGE, check_changes, names_folder  # noqa: E402
from assistant.schema import TOOL_NAMES  # noqa: E402


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
                _, blocked = check_changes(args.get("changes", []), facts["files"], user)
                errors += [f"{b.get('file')}: {b['blocked']}" for b in blocked]
                if not args.get("changes"):
                    errors.append("propose_changes with no changes (leave the call out instead)")
    last = msgs[-1]
    if last["role"] != "assistant":
        errors.append("must end with an assistant message")
    if proposed and NO_CHANGE.search(user):
        errors.append("proposed changes although the user asked for no changes")
    return errors


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
