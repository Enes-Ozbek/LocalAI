"""Review the teacher's drafts one by one. Nothing reaches training without a human "yes".

    python dataset/review.py

For each draft: a = accept, r = reject, e = edit (opens the record in Notepad; save and close),
q = quit (your progress is kept). Accepted records go to dataset/reviewed.jsonl.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from validate import check  # noqa: E402

HERE = Path(__file__).resolve().parent
DRAFTS, REVIEWED, DROPPED = HERE / "drafts.jsonl", HERE / "reviewed.jsonl", HERE / "dropped.jsonl"


def show(rec):
    msgs = rec["messages"]
    print("\n" + "=" * 78)
    print(f"{rec['meta']['task_id']}  ({rec['meta']['request_type']})")
    print(f"USER: {msgs[1]['content']}")
    for m in msgs[2:]:
        if m["role"] == "tool":
            facts = json.loads(m["content"])
            print(f"FOLDER {facts['folder']}  subfolders={facts['subfolders']}")
            for f in facts["files"]:
                extra = f" [dup of {f['duplicate_of']}]" if "duplicate_of" in f else ""
                text = f"  \"{f['text_start'][:70]}\"" if f.get("text_start") else ""
                print(f"  - {f['file']}  ({f['type']}, {f['age_days']}d){extra}{text}")
        elif m["role"] == "assistant":
            if m.get("content"):
                print(f"ASSISTANT: {m['content']}")
            for tc in m.get("tool_calls", []):
                name, args = tc["function"]["name"], tc["function"]["arguments"]
                if name == "propose_changes":
                    for c in args["changes"]:
                        target = c.get("to_folder", "") + ("/" if c.get("to_folder") else "") + c.get("new_name", "")
                        print(f"    {c['action'].upper():6} {c['file']}  ->  {target or '-'}   ({c['reason']})")
                elif name != "inspect_folder":
                    print(f"  {name}: {args}")


def edit(rec):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(rec, tmp, indent=2, ensure_ascii=False)
    editor = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "nano")
    subprocess.run([editor, tmp.name])
    with open(tmp.name, encoding="utf-8") as f:
        return json.load(f)


def main():
    done = set()
    for path in (REVIEWED, DROPPED):
        if path.exists():
            done |= {json.loads(line)["meta"]["task_id"] for line in open(path, encoding="utf-8") if line.strip()}
    drafts = [json.loads(line) for line in open(DRAFTS, encoding="utf-8") if line.strip()]
    todo = [d for d in drafts if d["meta"]["task_id"] not in done]
    print(f"{len(done)} reviewed, {len(todo)} to go.")
    for rec in todo:
        show(rec)
        while True:
            choice = input("[a]ccept  [r]eject  [e]dit  [q]uit > ").strip().lower()
            if choice == "e":
                rec = edit(rec)
                errors = check(rec)
                show(rec)
                if errors:
                    print("Still has problems: " + "; ".join(errors))
                continue
            if choice == "a" and check(rec):
                print("Can't accept: " + "; ".join(check(rec)) + ". Edit or reject it.")
                continue
            if choice in ("a", "r", "q"):
                break
        if choice == "q":
            break
        rec["status"] = "accepted" if choice == "a" else "rejected"
        with open(REVIEWED if choice == "a" else DROPPED, "a", encoding="utf-8") as out:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
