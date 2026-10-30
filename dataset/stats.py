"""Counts the data a model was trained and tested on, for the report and the pre-assessment form.

    python dataset/stats.py                          # dataset/train.jsonl, val.jsonl, test.jsonl
    python dataset/stats.py --md results/data_stats.md

Counted per split:
  conversations    one user request with the whole answer
  examples         training examples: one per assistant turn (what train/sft_data.py makes)
  file decisions   one file in a request that asks for changes (organize, one-file and move requests):
                   the answer moves, renames or deletes it, or leaves it where it is
  changes          files the answer moves, renames or deletes
  questions        conversations where the model answers a question with the read-only tools
"""

import argparse
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHANGE_REQUESTS = {"organize", "inspect_explain_organize", "organize_limit", "organize_rule", "one_file", "move_elsewhere"}
SPLITS = ("train", "val", "test")
DEFINITIONS = """- **Conversation:** one user request with the whole answer.
- **Example:** one assistant turn, the unit the model is trained on.
- **File decision:** one file in a request that asks for changes (organize, one-file and move requests). The answer
  moves, renames or deletes it, or leaves it where it is.
- **Change:** a file the answer moves, renames or deletes.
- **Question:** a conversation where the model answers a question about files with the read-only tools
  (count, sort, filter, search), made by dataset/make_questions.py.
"""


def count(path: Path) -> dict:
    out = Counter()
    kinds = Counter()
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        rec = json.loads(line)
        kind = rec.get("meta", {}).get("request_type", "other")
        kinds[kind] += 1
        msgs = rec["messages"]
        out["conversations"] += 1
        first = max(i for i, m in enumerate(msgs) if m["role"] == "user") if rec.get("mode") == "question" else 0
        out["examples"] += sum(m["role"] == "assistant" for m in msgs[first:])  # as train/sft_data.py counts
        if kind in CHANGE_REQUESTS:
            out["file decisions"] += sum(len(json.loads(m["content"])["files"]) for m in msgs if m["role"] == "tool")
        if rec.get("mode") == "question":
            out["questions"] += 1
        out["changes"] += sum(len(tc["function"]["arguments"].get("changes", []))
                              for m in msgs for tc in m.get("tool_calls") or []
                              if tc["function"]["name"] == "propose_changes")
    return {**out, "kinds": dict(kinds)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="jsonl files (default: the train, val and test splits)")
    ap.add_argument("--md", help="also write the table to this Markdown file")
    args = ap.parse_args()
    paths = [Path(f) for f in args.files] or [HERE / f"{s}.jsonl" for s in SPLITS if (HERE / f"{s}.jsonl").exists()]
    cols = ("conversations", "examples", "file decisions", "changes", "questions")
    rows, total = [], Counter()
    for p in paths:
        c = count(p)
        total.update({k: c.get(k, 0) for k in cols})
        rows.append((p.stem, c))
    lines = ["| Split | " + " | ".join(cols) + " | Request types |", "|" + " --- |" * (len(cols) + 2)]
    for name, c in rows:
        kinds = ", ".join(f"{k} {v}" for k, v in sorted(c["kinds"].items(), key=lambda kv: -kv[1]))
        lines.append(f"| {name} | " + " | ".join(f"{c.get(k, 0):,}" for k in cols) + f" | {kinds} |")
    lines.append("| **total** | " + " | ".join(f"**{total[k]:,}**" for k in cols) + " | |")
    table = "\n".join(lines)
    print(table)
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text("# Data used for training and testing\n\n" + table + "\n\n" + DEFINITIONS, encoding="utf-8")


if __name__ == "__main__":
    main()
