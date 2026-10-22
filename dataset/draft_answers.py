"""A bigger "teacher" model drafts the answer for each task. You review them afterwards.

    python dataset/draft_answers.py --teacher qwen3.5:27b
    python dataset/draft_answers.py --teacher qwen3.5:9b --limit 20     # quick try

Reads dataset/tasks.jsonl and writes:
  dataset/drafts.jsonl     records that pass validate.py, waiting for human review
  dataset/rejected.jsonl   drafts that broke a rule, with the reasons
Run it on a machine with a big GPU (for example Colab with Ollama). It resumes where it stopped.

The teacher should be bigger than the 4B student, otherwise the student only learns its own mistakes.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant import llm  # noqa: E402
from assistant.schema import SYSTEM_PROMPT, tool_result  # noqa: E402
from validate import check  # noqa: E402

HERE = Path(__file__).resolve().parent

TEACHER_PROMPT = """You write the ideal answer of a file-organizing assistant on a Windows PC, as training data.
You get the user's request and facts about every file in the folder. Return JSON with:
- "explanation": what the files are, in plain English, grouped (for example "3 lecture slides for
  CSE 301", "2 phone bills"). 2 to 6 short lines. Then one sentence on what you suggest.
- "changes": one entry per file to change: {"file", "action": "move"|"rename"|"delete",
  "to_folder" (for move), "new_name" (optional, keeps the extension), "reason" (short)}.
Rules:
- Use the file contents (text_start), names, types and dates. Give unclear names (scan_0031.pdf,
  document(3).pdf) a clear new name based on their content.
- Group files into a few sensible subfolders. Reuse existing subfolders when they fit. Same kind of
  file -> same folder name.
- Delete only exact duplicates (duplicate_of), old installers, temporary or broken downloads
  (.tmp, .crdownload, .part) and empty notes. Never delete anything changed in the last 14 days
  unless it is an exact duplicate. When unsure, leave the file alone (no entry).
- Follow every limit in the request exactly ("don't delete" -> no delete at all; "only the pdfs" ->
  only change PDFs; "leave the photos" -> no change to images; "by year" -> folders named by year).
- If the user only wants to know what is there, "changes" must be [].
- Never invent files. Use each file at most once."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "explanation": {"type": "string"},
        "changes": {"type": "array", "items": {"type": "object", "properties": {
            "file": {"type": "string"},
            "action": {"type": "string", "enum": ["move", "rename", "delete"]},
            "to_folder": {"type": "string"},
            "new_name": {"type": "string"},
            "reason": {"type": "string"}},
            "required": ["file", "action", "reason"]}}},
    "required": ["explanation", "changes"],
}

CLARIFY_QUESTION = "Which folder do you mean? You can type its name or path, for example 'Downloads' or 'Desktop/new folder'."
REFUSE_SYSTEM = ("{x} is a Windows system folder. Changing files there can break Windows or your programs, "
                 "so I won't touch it.")
REFUSE_OFFTOPIC = "I can only help you look at and organize the files in your folders."


def tool_call(name, **args):
    return {"type": "function", "function": {"name": name, "arguments": args}}


def build_record(task, answer=None):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": task["user"]}]
    rtype = task["request_type"]
    if rtype == "clarify":
        msgs.append({"role": "assistant", "content": "", "tool_calls": [tool_call("ask_clarification", question=CLARIFY_QUESTION)]})
    elif rtype == "refuse":
        system_word = next((w for w in ("System32", "Program Files", "Windows", "AppData") if w.lower() in task["user"].lower()), None)
        reason = REFUSE_SYSTEM.format(x=system_word) if system_word else REFUSE_OFFTOPIC
        msgs.append({"role": "assistant", "content": "", "tool_calls": [tool_call("refuse", reason=reason)]})
    else:
        msgs.append({"role": "assistant", "content": "", "tool_calls": [tool_call("inspect_folder", folder=task["folder"])]})
        msgs.append({"role": "tool", "tool_name": "inspect_folder", "content": tool_result(task["facts"])})
        final = {"role": "assistant", "content": answer["explanation"].strip()}
        if answer["changes"] and rtype != "explain_only":
            final["tool_calls"] = [tool_call("propose_changes", folder=task["folder"], changes=answer["changes"])]
        msgs.append(final)
    return {"meta": {k: task[k] for k in ("task_id", "scenario_id", "persona", "request_type")},
            "status": "needs_review", "messages": msgs}


def ask_teacher(task, teacher):
    prompt = f"User request: {task['user']}\n\nFolder facts:\n{json.dumps(task['facts'], ensure_ascii=False)}"
    if task["request_type"] == "explain_only":
        prompt += "\n\nThe user wants no changes: \"changes\" must be []."
    resp = llm.chat([{"role": "system", "content": TEACHER_PROMPT}, {"role": "user", "content": prompt}],
                    model=teacher, format=ANSWER_SCHEMA, options={"num_ctx": 16384, "temperature": 0.3})
    answer = json.loads(resp.message.content)
    for c in answer["changes"]:  # drop empty optional fields the model sometimes adds
        for key in ("to_folder", "new_name"):
            if not c.get(key):
                c.pop(key, None)
    return answer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--teacher", required=True, help="Ollama model name, bigger than the student")
    ap.add_argument("--tasks", default=str(HERE / "tasks.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    drafts, rejected = HERE / "drafts.jsonl", HERE / "rejected.jsonl"
    done = set()
    for path in (drafts, rejected):
        if path.exists():
            done |= {json.loads(line)["meta"]["task_id"] for line in open(path, encoding="utf-8") if line.strip()}

    tasks = [json.loads(line) for line in open(args.tasks, encoding="utf-8") if line.strip()]
    todo = [t for t in tasks if t["task_id"] not in done][: args.limit or None]
    print(f"{len(done)} already drafted, {len(todo)} to go, teacher = {args.teacher}")
    ok = bad = 0
    for i, task in enumerate(todo, 1):
        start = time.perf_counter()
        try:
            answer = None if task["request_type"] in ("clarify", "refuse") else ask_teacher(task, args.teacher)
            rec = build_record(task, answer)
            errors = check(rec)
        except Exception as e:  # a broken teacher answer is rejected, not fatal
            rec, errors = {"meta": {"task_id": task["task_id"]}, "task": task}, [f"teacher failed: {e}"]
        target = rejected if errors else drafts
        if errors:
            rec["errors"] = errors
            bad += 1
        else:
            ok += 1
        with open(target, "a", encoding="utf-8") as out:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[{i}/{len(todo)}] {task['task_id']} {task['request_type']:25} "
              f"{'REJECTED' if errors else 'ok'} ({time.perf_counter() - start:.1f}s)")
    print(f"Done: {ok} drafts, {bad} rejected. Next: python dataset/review.py")


if __name__ == "__main__":
    main()
