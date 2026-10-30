"""Asks the model questions about the sandbox and checks the answers against exact counts. For the report.

    python make_sandbox.py
    python bench/questions.py
    python bench/questions.py --model qwen3.5:4b-q4_K_M --limit 5

Each question goes through agent.Session like in the app: the model picks read-only tools (list_files,
folder_summary, search_files), code counts and sorts, and the model answers. Follow-ups ('and desktop?') run
in the same session as the question before them. Metrics:
 - right answer: the expected number or file name is in the answer or the files shown
 - answered by the model: it used a tool and answered itself (otherwise the code answer was the backup)
 - tool calls and seconds per question
Writes results/questions_<model>.csv.
"""

import argparse
import csv
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from assistant import agent, llm, scanner  # noqa: E402


def files(base: Path, folder: str, deep: bool = False) -> list:
    root = base / folder
    return [p for p in (root.rglob("*") if deep else root.iterdir()) if p.is_file()]


def every(base: Path) -> list:
    return [p for top in base.iterdir() if top.is_dir() for p in top.rglob("*") if p.is_file()]


def size_text(paths: list) -> str:
    kb = sum(p.stat().st_size for p in paths) / 1024
    return f"{kb / 1024:.1f}" if kb >= 1024 else f"{kb:.0f}"  # '105' matches '105.3 KB' and '105 KB'


# (question, how to find the expected answer, follow-up of the one before)
CASES = [
    ("how many files are there?", lambda b: str(len(every(b))), False),
    ("how many files are in downloads?", lambda b: (str(len(files(b, "Downloads"))),
                                                    str(len(files(b, "Downloads", True)))), False),
    ("and desktop?", lambda b: str(len(files(b, "Desktop"))), True),
    ("how many pdfs are in archive?", lambda b: str(sum(p.suffix == ".pdf" for p in files(b, "Archive"))), False),
    ("how many photos are in archive?", lambda b: str(sum(scanner.type_of(p) == "images"
                                                          for p in files(b, "Archive"))), False),
    ("what's the biggest file in archive?", lambda b: max(files(b, "Archive"), key=lambda p: p.stat().st_size).name,
     False),
    ("what's the newest file in downloads?", lambda b: max(files(b, "Downloads"),
                                                           key=lambda p: p.stat().st_mtime).name, False),
    ("which is the oldest file in archive?", lambda b: min(files(b, "Archive"), key=lambda p: p.stat().st_mtime).name,
     False),
    ("how big is my desktop?", lambda b: size_text(files(b, "Desktop")), False),
    ("how many installers are in downloads?", lambda b: str(sum(p.suffix in (".exe", ".msi")
                                                                for p in files(b, "Downloads"))), False),
    ("how many duplicates are in archive?", lambda b: str(len(scanner.find_duplicates(files(b, "Archive")))), False),
    ("how many screenshots are on my desktop?", lambda b: str(sum("screenshot" in p.name.lower()
                                                                  for p in files(b, "Desktop"))), False),
    ("find my cv", lambda b: "CV_Enes_2025.docx", False),
    ("where are my lecture notes?", lambda b: "lecture_", False),
    ("is there a bank statement anywhere?", lambda b: "bank_statement", False),
    ("how many files are in archive?", lambda b: str(len(files(b, "Archive"))), False),
    ("how many of them are pdfs?", lambda b: str(sum(p.suffix == ".pdf" for p in files(b, "Archive"))), True),
]


def right(r, expected) -> bool:
    """The expected number ('192', not inside '2026-10-19') or file name is in the answer or the files shown."""
    said = (r.text + " " + " ".join(m["name"] for m in r.matches[:10])).replace(",", "")
    for e in expected if isinstance(expected, tuple) else (expected,):
        if re.fullmatch(r"[\d.]+", e):
            if re.search(r"(?<![\d.-])" + re.escape(e) + r"(?![\d-])", said):
                return True
        elif e.lower() in said.lower():
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(ROOT / "sandbox"))
    ap.add_argument("--model", default=llm.MODEL)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    base = Path(args.base).resolve()
    if not (base / "Archive").is_dir():
        sys.exit("Run python make_sandbox.py first.")
    agent.QUESTION_MODE = "model"
    cases = CASES[:args.limit] if args.limit else CASES
    rows, session = [], None
    for question, expect, follow_up in cases:
        if not follow_up:
            session = agent.Session(base, args.model)
        expected = expect(base)
        t = time.perf_counter()
        r = session.run(question)
        seconds = round(time.perf_counter() - t, 1)
        ok, by_model = right(r, expected), not r.by_code
        tools = "; ".join(f"{c['tool']}({', '.join(f'{k}={v}' for k, v in c.items() if k != 'tool')})"
                          for c in r.tools)
        rows.append({"question": question, "expected": " or ".join(expected) if isinstance(expected, tuple)
                     else expected, "right": int(ok), "answered_by_model": int(by_model), "tools": tools,
                     "answer": r.text.replace("\n", " "), "seconds": seconds})
        print(f"{'OK  ' if ok else 'MISS'} {'model' if by_model else 'code '} {seconds:5.1f}s  {question}\n"
              f"      tools: {tools or '-'}\n      answer: {r.text[:160]}")
    n = len(rows)
    print(f"\nRight answers: {sum(r['right'] for r in rows)}/{n}")
    print(f"Answered by the model itself: {sum(r['answered_by_model'] for r in rows)}/{n} "
          f"(right: {sum(r['right'] for r in rows if r['answered_by_model'])})")
    print(f"Median time: {statistics.median(r['seconds'] for r in rows)} s")
    name = re.sub(r"[^\w.-]+", "_", llm.label(args.model))
    out = ROOT / "results" / f"questions_{name}.csv"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
