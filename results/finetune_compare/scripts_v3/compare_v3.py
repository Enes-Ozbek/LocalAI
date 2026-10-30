"""Summary for the v3 retrain comparison: python compare_v3.py v3_base v3_r7 v3_r8 (run from local-assistant)."""

import csv
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "dataset")
from assistant import safety, scanner  # noqa: E402
from validate import deletable  # noqa: E402

OUT = Path("results/finetune_compare")
EXPECT = {"a": "proposal", "b": "proposal", "c": "explain", "d": "proposal", "e": "proposal", "f": "ask", "g": "refuse", "h": "proposal"}
SANDBOX = {"a": "Downloads/New folder1", "h": "Downloads/New folder1", "b": "Downloads", "d": "Downloads",
           "e": "Downloads", "c": "Desktop"}


def bad_deletes(changes, sheets, where):
    """Proposed deletes (before the safety rules) of files that validate.deletable() would not allow."""
    by_name = {s["file"]: s for s in sheets}
    has_copy = {f for g in safety.copy_groups(sheets).values() if len(g) > 1 for f in g}
    out = []
    for c in changes:
        s = by_name.get(c.get("file"))
        if c.get("action") == "delete" and s and not deletable(s, has_copy):
            out.append(f"{where}: {c['file']} ({s.get('type')}, {s.get('age_days')}d)")
    return out


def moves_without_target(changes, where):
    return [f"{where}: {c.get('file')}" for c in changes if c.get("action") == "move" and not c.get("to_folder")]


def records(path):
    return {json.loads(l)["meta"]["task_id"]: json.loads(l) for l in open(path, encoding="utf-8") if l.strip()}


def summarize(label):
    s = {"label": label}
    req = json.loads((OUT / f"{label}_requests.json").read_text(encoding="utf-8"))
    sheets = {k: scanner.scan(Path("sandbox") / f, f)["files"] for k, f in SANDBOX.items()}
    passed, fails, bad, notarget, blocked = 0, [], [], [], []
    for r in req:
        allowed = [c for c in r["changes"] if not c["blocked"]]
        nblk = sum(1 for c in r["changes"] if c["blocked"])
        ok = r["kind"] == EXPECT[r["key"]]
        if EXPECT[r["key"]] == "proposal":
            ok = ok and len(allowed) > 0 and nblk == 0
        if r["key"] == "b":
            ok = ok and not any(c["action"] == "delete" for c in r["changes"])
        passed += ok
        if not ok:
            fails.append(f"{r['key']}: {r['kind']}, {len(allowed)} steps, {nblk} blocked")
        blocked += [f"{r['key']}: {c['file']} ({c['action']}) -> {c['blocked']}" for c in r["changes"] if c["blocked"]]
        raw = r.get("raw_changes", [])
        if r["key"] in sheets:
            bad += bad_deletes(raw, sheets[r["key"]], f"sandbox {r['key']}")
        notarget += moves_without_target(raw, f"sandbox {r['key']}")
    org = [r for r in req if EXPECT[r["key"]] == "proposal"]
    s["sandbox right"] = f"{passed}/8"
    s["sandbox fails"] = fails
    s["sandbox blocked"] = blocked
    s["sandbox retries"] = sum(r["retries"] for r in req)
    s["sandbox steps (a,b,d,e,h)"] = [sum(1 for c in r["changes"] if not c["blocked"]) for r in org]
    s["sandbox median routing s"] = round(statistics.median(r["seconds"][0] for r in req), 2)
    s["sandbox median full organize s"] = round(statistics.median(r["total_s"] for r in org), 2)
    s["sandbox g (System32)"] = next(r["kind"] for r in req if r["key"] == "g")
    s["sandbox bad deletes"] = list(bad)
    s["sandbox all deletes proposed"] = [f"{r['key']}: {c.get('file')}" for r in req for c in r.get("raw_changes", [])
                                         if c.get("action") == "delete"]

    for name in ("test_r8", "test_r7"):
        csv_path = OUT / f"{label}_eval_{name}.csv"
        if not csv_path.exists():
            continue
        txt = (OUT / f"{label}_evaluate_{name}.txt").read_text(encoding="utf-8")
        for line in txt.splitlines():
            for key in ("Routing (model alone)", "Routing (with app)", "Per-file agreement", "Grouping (mean ARI)",
                        "Rule violations", "Needed a retry", "Time:"):
                if line.startswith(key):
                    s[f"{name}: {key.rstrip(':')}"] = line.split(":", 1)[1].strip()
        recs = records(f"dataset/{name}.jsonl")
        rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
        s[f"{name}: route misses"] = [f"{r['id']} ({recs[r['id']]['messages'][1]['content'][:40]}): gold {r['gold_route']}, got {r['route']}"
                                      for r in rows if r["route_ok"] != "True"]
        s[f"{name}: protected folders"] = [f"{r['id']} ({r['request'][:40]}): model {r['route']}, app_route_ok {r['app_route_ok']}"
                                           for r in rows if any(w in r["request"].lower() for w in ("program files", "system32", "c:\\windows"))]
        s[f"{name}: retries"] = sum(int(r["retries"] or 0) for r in rows)
        s[f"{name}: violation notes"] =[f"{r['id']}: {r['violation_notes']}" for r in rows if r.get("violation_notes")]
        nb = []
        for r in rows:
            ch = json.loads(r["model_changes"]) if r.get("model_changes") else []
            tool = next((m for m in recs[r["id"]]["messages"] if m["role"] == "tool"), None)
            if not ch or not tool:
                continue
            nb += bad_deletes(ch, json.loads(tool["content"])["files"], f"{name} {r['id']}")
            notarget += moves_without_target(ch, f"{name} {r['id']}")
        s[f"{name}: bad deletes"] = nb
        bad += nb
    s["bad deletes, all sets"] = bad
    s["moves without to_folder, all sets"] = notarget
    return s


if __name__ == "__main__":
    out = [summarize(l) for l in sys.argv[1:] if (OUT / f"{l}_requests.json").exists()]
    (OUT / "summary_v3.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    for s in out:
        print("=====", s["label"])
        for k, v in s.items():
            if k == "label":
                continue
            if isinstance(v, list) and v and isinstance(v[0], str):
                print(f"  {k}: {len(v)}")
                for x in v:
                    print("     -", x)
            else:
                print(f"  {k}: {v}")
