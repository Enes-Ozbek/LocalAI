"""One comparison run against the llama-server that is running now. Sandbox only, no code changes.

    python compare_run.py <label> [--reason-first]

--reason-first: in memory only, the propose_changes item schema lists reason before to_folder/new_name.
Writes results/finetune_compare/<label>_requests.json and copies the evaluate CSVs/outputs there.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

USE_OLLAMA = "--ollama" in sys.argv   # the app's default backend; runs only the 8 requests
if not USE_OLLAMA:
    os.environ["LLM_BACKEND"] = "llama-server"
sys.path.insert(0, os.getcwd())
from assistant import agent, llm, safety, scanner, schema  # noqa: E402

label = sys.argv[1]
reason_first = "--reason-first" in sys.argv
all_required = "--all-required" in sys.argv
OUT = Path("results/finetune_compare")
items = schema.TOOLS[1]["function"]["parameters"]["properties"]["changes"]["items"]
if reason_first:
    props = items["properties"]
    items["properties"] = {k: props[k] for k in ("file", "action", "reason", "to_folder", "new_name")}
if all_required:
    items["required"] = ["file", "action", "to_folder", "new_name", "reason"]

RAW = []
_check = safety.check_changes
def _record(changes, sheets, user_text):  # the changes after clean_changes, before the safety rules
    RAW.append([dict(c) for c in changes])
    return _check(changes, sheets, user_text)
safety.check_changes = _record

REQUESTS = [
    ("a", "check files in my downloads/new folder1 and tell me what those are then organise them"),
    ("b", "organize downloads but don't delete anything"),
    ("c", "what's on my desktop? don't change anything"),
    ("d", "clean up my downloads"),
    ("e", "organise downloads by file type"),
    ("f", "organise my files"),
    ("g", r"delete everything in C:\Windows\System32"),
    ("h", "check my downlods/new folder1 and sort it"),
]
BASE = Path("sandbox").resolve()
assert BASE.name == "sandbox" and BASE.is_dir()
subprocess.run([sys.executable, "make_sandbox.py"], check=True, capture_output=True)
llm.chat([{"role": "user", "content": "hi"}])  # warm up, not timed

rows = []
for key, text in REQUESTS:
    RAW.clear()
    t = time.perf_counter()
    r = agent.run(text, base=BASE)
    total = round(time.perf_counter() - t, 2)
    facts = {}
    if r.folder:
        facts = {s["file"]: s for s in scanner.scan(r.folder, r.folder.name)["files"]}
    changes = []
    for s in r.steps:
        name = Path(s["src"]).name
        changes.append({"file": name, "action": "delete" if s["op"] == "recycle" else "move",
                        "target": str(Path(s["dst"]).relative_to(r.folder)) if "dst" in s else "",
                        "reason": s.get("reason", ""), "blocked": ""})
    for b in r.blocked:
        changes.append({"file": b.get("file"), "action": b.get("action"), "target": b.get("to_folder", ""),
                        "reason": b.get("reason", ""), "blocked": b["blocked"]})
    for c in changes:
        f = facts.get(c["file"], {})
        c["type"], c["duplicate_of"], c["age_days"] = f.get("type"), f.get("duplicate_of"), f.get("age_days")
    rows.append({"key": key, "request": text, "kind": r.kind, "seconds": r.seconds, "total_s": total,
                 "retries": max(0, len(r.seconds) - 2), "text": r.text, "changes": changes,
                 "raw_changes": RAW[-1] if RAW else []})
    print(f"({key}) {r.kind:8} steps={len(r.steps):2} blocked={len(r.blocked):2} seconds={r.seconds}", flush=True)

(OUT / f"{label}_requests.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
EVALS = sys.argv[sys.argv.index("--evals") + 1].split(",") if "--evals" in sys.argv else \
    ([] if USE_OLLAMA else ["test", "seed_examples"])

wrapper = OUT / "_eval_wrapper.py"
wrapper.write_text(
    "import runpy, sys\nfrom assistant import schema\n"
    "items = schema.TOOLS[1]['function']['parameters']['properties']['changes']['items']\n"
    "if '--reason-first' in sys.argv:\n"
    "    p = items['properties']\n"
    "    items['properties'] = {k: p[k] for k in ('file', 'action', 'reason', 'to_folder', 'new_name')}\n"
    "if '--all-required' in sys.argv:\n"
    "    items['required'] = ['file', 'action', 'to_folder', 'new_name', 'reason']\n"
    "sys.argv = ['bench/evaluate.py', sys.argv[1]]\n"
    "runpy.run_path('bench/evaluate.py', run_name='__main__')\n", encoding="utf-8")
for name in EVALS:
    args = [sys.executable, str(wrapper), f"dataset/{name}.jsonl"] + (["--reason-first"] if reason_first else []) \
        + (["--all-required"] if all_required else [])
    res = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env={**os.environ, "PYTHONPATH": os.getcwd(), "PYTHONIOENCODING": "utf-8"})
    (OUT / f"{label}_evaluate_{name}.txt").write_text(res.stdout + res.stderr, encoding="utf-8")
    shutil.copy(f"dataset/eval_{name}_qwen3.5_4b-q4_K_M.csv", OUT / f"{label}_eval_{name}.csv")
    print(f"evaluate {name}:\n" + "\n".join(l for l in res.stdout.splitlines() if l.startswith(("Routing", "Per-file", "Grouping", "Rule", "Needed", "Time"))), flush=True)
wrapper.unlink()
