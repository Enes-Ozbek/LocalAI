"""Leftovers of uninstalled apps: how well the model sorts app folders. Nothing on disk is moved or deleted here.

    python make_sandbox.py
    python bench/leftovers.py                         # the sandbox's fake PC, scored against make_sandbox.APPS
    python bench/leftovers.py --real                  # your own AppData and dot-folders: a report to check by hand

Sandbox: every app folder has a known answer (leftover, cache, ask, keep). The run goes through leftovers.plan, so
the model and the safety rules are scored together, as in the app. Metrics:
 - leftovers found: of the real leftovers, how many would be removed (ticked, or held back only because they
   changed recently, which the rules do on purpose)
 - wrong removals: folders of installed programs or with the user's own files that would be removed if the
   user pressed Clean up without looking. This is the number that matters most; it should be 0.
 - right answers overall, seconds
Real: writes results/leftovers_real_<date>.csv with the facts, the model's verdict, what the rules did and an
empty 'right?' column for you to fill in. That file becomes our real test set.
Writes results/leftovers_<model>.csv for the sandbox.
"""

import argparse
import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from assistant import leftovers, llm  # noqa: E402

REQUEST = "find leftovers of apps I uninstalled"


def outcome(folder: str, plan: dict) -> str:
    """What the app would do with the folder: remove (ticked), empty (a ticked cache), look (unticked),
    held (blocked by a rule) or keep (the model left it out)."""
    step = next((s for s in plan["steps"] if s["folder"] == folder), None)
    if step:
        return ("empty" if step["contents_only"] else "remove") if step["checked"] else "look"
    held = next((b for b in plan["blocked"] if b["folder"] == folder), None)
    return f"held: {held['blocked']}" if held else "keep"


def right(truth: str, did: str) -> bool:
    if truth == "leftover":
        return did == "remove" or did.startswith("held: changed")
    if truth == "cache":
        return did in ("empty", "remove")
    if truth == "ask":
        return did == "look" or did.startswith("held")
    return did in ("keep", "look") or did.startswith("held")


def sandbox(model: str, base: Path):
    import make_sandbox
    truth = {f: what for f, _, what in make_sandbox.APPS if what != "never"}
    t = time.perf_counter()
    plan = leftovers.plan(REQUEST, base, model)
    seconds = time.perf_counter() - t
    rows, harmful = [], 0
    for folder, what in sorted(truth.items()):
        did = outcome(folder, plan)
        bad = what in ("keep", "ask") and did in ("remove", "empty")
        harmful += bad
        step = next((s for s in plan["steps"] if s["folder"] == folder), {})
        rows.append({"folder": folder, "truth": what, "did": did, "right": right(what, did), "wrong_removal": bad,
                     "verdict": step.get("verdict", ""), "app": step.get("app", ""), "reason": step.get("reason", "")})
    lefts = [r for r in rows if r["truth"] == "leftover"]
    found = sum(r["right"] for r in lefts)
    name = llm.label(model).replace(":", "_").replace("/", "_")
    out = ROOT / "results" / f"leftovers_{name}.csv"
    _write(out, rows)
    print(f"{'folder':42} {'truth':9} did")
    for r in rows:
        mark = "  WRONG REMOVAL" if r["wrong_removal"] else "" if r["right"] else "  (miss)"
        print(f"{r['folder']:42} {r['truth']:9} {r['did']}{mark}")
    print(f"\nLeftovers found: {found}/{len(lefts)}   wrong removals: {harmful}   "
          f"right overall: {sum(r['right'] for r in rows)}/{len(rows)}   "
          f"{seconds:.0f} s for {plan['looked']} folders ({len(plan['seconds'])} model calls)\nWrote {out}")


def real(model: str):
    base = Path.home()
    t = time.perf_counter()
    plan = leftovers.plan(REQUEST, base, model, progress=lambda text: print(text, flush=True))
    seconds = time.perf_counter() - t
    sheets, _ = leftovers.scan(base)  # again, for the facts of the folders the model kept
    rows = []
    for s in sheets:
        step = next((x for x in plan["steps"] if x["folder"] == s["folder"]), {})
        held = next((b for b in plan["blocked"] if b["folder"] == s["folder"]), {})
        rows.append({"folder": s["folder"], "size_mb": s["size_mb"], "files": s["files"],
                     "last_change_days": s["last_change_days"], "kinds": s["kinds"],
                     "closest_installed": "; ".join(f"{m['program']} {m['score']}" for m in s["closest_installed"]),
                     "in_use": s["used_by_running_program"], "verdict": step.get("verdict") or held.get("verdict") or "keep",
                     "app": step.get("app") or held.get("app", ""), "reason": step.get("reason") or held.get("reason", ""),
                     "app_would": outcome(s["folder"], plan), "right?": ""})
    out = ROOT / "results" / f"leftovers_real_{time.strftime('%Y-%m-%d')}.csv"
    _write(out, rows)
    print(f"\n{plan['text']}\n{seconds:.0f} s, {len(plan['seconds'])} model calls. Nothing was moved.\n"
          f"Wrote {out}: fill in the 'right?' column (y / n and what it should be).")


def _write(path: Path, rows: list):
    path.parent.mkdir(exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=llm.MODEL)
    ap.add_argument("--base", default="sandbox")
    ap.add_argument("--real", action="store_true", help="your own home folder; nothing is moved")
    args = ap.parse_args()
    llm.start()
    try:
        real(args.model) if args.real else sandbox(args.model, Path(args.base).resolve())
    finally:
        llm.stop()


if __name__ == "__main__":
    main()
