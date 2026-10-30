"""Scores a model against hand-checked answers. This produces the numbers for the report.

    python bench/evaluate.py dataset/test.jsonl
    python bench/evaluate.py dataset/seed_examples.jsonl --model qwen3.5:9b

For every record it asks the model two things, exactly like the app does:
 1. Routing: given only the request, does it call the right tool (inspect / ask / refuse)?
 2. Decisions: given the same folder facts as the correct answer, what does it propose?
Metrics:
 - routing accuracy, for the model alone and with the app's checks (protected folders are refused,
   a folder the user never named turns into a question)
 - per-file action agreement (keep / move / rename / delete, compared with the correct answer)
 - grouping agreement: Adjusted Rand Index of "which files end up together" (1 = same grouping,
   0 = no better than chance; folder names may differ)
 - rule violations: changes the app's safety rules would block, or changes the user said not to make
 - response time per step
Writes a CSV with one row per record next to the input file.
"""

import argparse
import csv
import json
import statistics
import sys
import time
from collections import Counter
from math import comb
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant import agent, llm, safety, scanner  # noqa: E402
from assistant.schema import SYSTEM_PROMPT, TOOLS  # noqa: E402


def adjusted_rand_index(a: list, b: list) -> float:
    n = len(a)
    if n < 2:
        return 1.0
    pairs = Counter(zip(a, b))
    sum_ij = sum(comb(v, 2) for v in pairs.values())
    sum_a = sum(comb(v, 2) for v in Counter(a).values())
    sum_b = sum(comb(v, 2) for v in Counter(b).values())
    expected = sum_a * sum_b / comb(n, 2)
    max_index = (sum_a + sum_b) / 2
    if max_index == expected:  # both put everything together, or both keep everything apart
        return 1.0
    return (sum_ij - expected) / (max_index - expected)


def changes_of(message: dict) -> list:
    for tc in message.get("tool_calls") or []:
        if tc["function"]["name"] == "propose_changes":
            return tc["function"]["arguments"].get("changes", [])
    return []


def labels(files: list, changes: list) -> tuple:
    by_file = {c.get("file"): c for c in changes}
    actions, groups = [], []
    for f in files:
        c = by_file.get(f["file"])
        act = c.get("action", "keep") if c else "keep"
        actions.append(act)
        if act == "move":
            groups.append((c.get("to_folder") or "").strip("/\\ ").lower())
        elif act == "delete":
            groups.append("<deleted>")
        else:
            groups.append(".")
    return actions, groups


def app_route(first, user: str) -> str:
    """What the app does with the model's first step (see agent.run)."""
    if not first.tool_calls:
        return "(text)"
    name, args = first.tool_calls[0].function.name, dict(first.tool_calls[0].function.arguments)
    if name != "inspect_folder":
        return name
    folder = args.get("folder", "")
    if safety.is_protected(scanner.resolve_folder(folder, Path.home())) or safety.names_system_folder(folder):
        return "refuse"
    if not safety.names_folder(folder, user):
        return "ask_clarification"
    return name


def evaluate(rec: dict, model: str) -> dict:
    msgs = rec["messages"]
    user = msgs[1]["content"]
    gold_first = msgs[2]["tool_calls"][0]["function"]["name"] if msgs[2].get("tool_calls") else "(text)"
    row = {"id": rec.get("meta", {}).get("task_id", ""), "type": rec.get("meta", {}).get("request_type", ""),
           "request": user, "gold_route": gold_first}

    t = time.perf_counter()
    first = llm.chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
                     model=model, tools=TOOLS).message
    row["route_s"] = round(time.perf_counter() - t, 2)
    row["route"] = first.tool_calls[0].function.name if first.tool_calls else "(text)"
    row["route_ok"] = row["route"] == gold_first
    row["app_route_ok"] = app_route(first, user) == gold_first

    tool_idx = next((i for i, m in enumerate(msgs) if m["role"] == "tool"), None)
    if tool_idx is None:
        return row
    facts = json.loads(msgs[tool_idx]["content"])
    text, model_changes, seconds = agent.decide(msgs[:tool_idx + 1], user, model)
    row["decide_s"] = round(sum(seconds), 2)
    row["retries"] = len(seconds) - 1  # "go ahead" or "fix the format" calls (see agent.decide)
    model_changes = safety.clean_changes(model_changes or [], facts["folder"])

    gold_changes = changes_of(msgs[-1])
    ga, gg = labels(facts["files"], gold_changes)
    ma, mg = labels(facts["files"], model_changes)
    row["files"] = len(facts["files"])
    row["action_agree"] = sum(x == y for x, y in zip(ga, ma))
    row["ari"] = round(adjusted_rand_index(gg, mg), 3)
    _, blocked = safety.check_changes(model_changes, facts["files"], user)
    unwanted = len(model_changes) if (safety.NO_CHANGE.search(user) or row["type"] == "explain_only") else 0
    row["violations"] = len(blocked) + unwanted
    row["violation_notes"] = "; ".join(f"{b.get('file')}: {b['blocked']}" for b in blocked) + \
        ("; changes proposed although user wanted none" if unwanted else "")
    row["model_explanation"] = text[:300]
    row["model_changes"] = json.dumps(model_changes, ensure_ascii=False)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("records")
    ap.add_argument("--model", default=llm.MODEL)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    records = [json.loads(line) for line in open(args.records, encoding="utf-8") if line.strip()]
    records = records[: args.limit or None]
    llm.chat([{"role": "user", "content": "hi"}], model=args.model)  # load the model first, not timed
    rows = []
    for i, rec in enumerate(records, 1):
        row = evaluate(rec, args.model)
        rows.append(row)
        extra = f" files {row['action_agree']}/{row['files']} ARI {row['ari']} violations {row['violations']}" \
            if "files" in row else ""
        print(f"[{i}/{len(records)}] {row['id'] or i} route {'OK' if row['route_ok'] else 'MISS'} "
              f"({row['route']}){extra}")

    out = Path(args.records).with_name(f"eval_{Path(args.records).stem}_{llm.label(args.model).replace(':', '_').replace('/', '_')}.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r}))
        w.writeheader()
        w.writerows(rows)

    decided = [r for r in rows if "files" in r]
    print(f"\n== {args.model} on {len(rows)} records ({Path(args.records).name})")
    print(f"Routing (model alone): {sum(r['route_ok'] for r in rows)}/{len(rows)}")
    print(f"Routing (with app):    {sum(r['app_route_ok'] for r in rows)}/{len(rows)}")
    if decided:
        files = sum(r["files"] for r in decided)
        print(f"Per-file agreement:    {sum(r['action_agree'] for r in decided)}/{files} "
              f"({100 * sum(r['action_agree'] for r in decided) / files:.0f}%)")
        print(f"Grouping (mean ARI):   {statistics.mean(r['ari'] for r in decided):.2f}")
        print(f"Rule violations:       {sum(r['violations'] for r in decided)} "
              f"(in {sum(1 for r in decided if r['violations'])} records)")
        print(f"Needed a retry:        {sum(r['retries'] > 0 for r in decided)}/{len(decided)} records")
        print(f"Time: routing median {statistics.median(r['route_s'] for r in rows):.1f}s, "
              f"decisions median {statistics.median(r['decide_s'] for r in decided):.1f}s")
    print(f"Details: {out}")


if __name__ == "__main__":
    main()
