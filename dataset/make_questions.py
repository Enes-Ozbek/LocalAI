"""Makes training conversations for questions about files: the model calls one of the read-only tools
(schema.READ_TOOLS), then answers from the result in a sentence or two.

    python dataset/make_questions.py                  # 2 per folder in dataset/tasks.jsonl -> dataset/questions.jsonl
    python dataset/make_questions.py --per-scenario 3

Uses the made-up folders of tasks.jsonl, so no files and no teacher model are needed: the right tool call is
known for every question, and the tool results come from the functions the app uses (answers.list_result,
summary_result, overview_result), so training and use see the same text. Some conversations are follow-ups
('how many of them are pdfs?') with the question and answer before them, like the app's Session sends them.
split.py puts each conversation in the same split as its folder.
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant import answers  # noqa: E402
from assistant.schema import question_prompt  # noqa: E402
from make_scenarios import folder_mention  # noqa: E402

HERE = Path(__file__).resolve().parent
MAIN_FOLDERS = ["Downloads", "Desktop", "Documents", "Pictures", "Videos", "Music"]
# kind argument -> how people say it
KIND_WORDS = {"photos": ["photos", "pictures", "images"], "documents": ["documents", "docs"],
              "installers": ["installers", "setup files"], "videos": ["videos"], "screenshots": ["screenshots"],
              "spreadsheets": ["spreadsheets", "excel files"], "archives": ["zip files", "archives"],
              "duplicates": ["duplicates", "copies"], "temporary": ["unfinished downloads", "temp files"]}
EXTENSIONS = [".pdf", ".docx", ".jpg", ".png", ".exe", ".zip", ".txt", ".pptx", ".xlsx", ".mp4"]
GENERIC = {"file", "final", "copy", "scan", "img", "image", "document", "new", "old", "untitled", "download",
           "setup", "installer", "version", "draft", "screenshot", "photo", "video", "the", "and", "for", "with"}

QUESTIONS = {  # type: (weight, templates)
    "count": (18, ["how many files are in {f}?", "how many files does {f} have?", "count the files in {f}",
                   "how many files are there in {f}", "how many things are in {f}?"]),
    "count_kind": (20, ["how many {k} are in {f}?", "how many {k} do I have in {f}?", "count the {k} in {f}",
                        "are there any {k} in {f}?", "how many {k} are there in {f}"]),
    "position": (20, ["what's the biggest file in {f}?", "which file in {f} is the biggest?",
                      "what's the newest file in {f}?", "what is the last file I downloaded in {f}",
                      "what's the oldest file in {f}?", "which is the smallest file in {f}?",
                      "show me the {n} biggest files in {f}", "list the {n} newest files in {f}",
                      "what are the {n} largest files in {f}?"]),
    "size": (8, ["how big is {f}?", "how much space does {f} take?", "how much space do the {k} in {f} take?",
                 "what's the total size of the {k} in {f}?"]),
    "summary": (8, ["what kinds of files are in {f}?", "give me a summary of {f}", "what types of files does {f} have?",
                    "how many photos and documents are in {f}?"]),
    "date": (8, ["how many files in {f} are older than a year?", "which files in {f} are from the last week?",
                 "how many {k} in {f} are from the last month?", "list the files in {f} older than 6 months"]),
    "everywhere": (6, ["how many files are there?", "how many files do I have?", "which folder has the most files?",
                       "how much space do my folders take?"]),
    "search": (10, ["find my {q}", "where is my {q}?", "is there a {q} in {f}?", "find the {q} file",
                    "where did I put the {q}?"]),
    "follow_up": (15, []),  # a question about the folder above, after one about it
}


def items_of(facts: dict) -> list:
    """[(path, sheet)] for the made-up files, with the exact size and time the tools sort by."""
    folder = PurePosixPath(facts["folder"])  # 'Downloads/New folder1' on every system
    return [(folder / s["file"], {**s, "bytes": int(s["size_kb"] * 1024), "mtime": -s["age_days"] * 86400.0})
            for s in facts["files"]]


def call(name: str, **args) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def tool(name: str, result: dict) -> dict:
    return {"role": "tool", "tool_name": name, "content": json.dumps(result, ensure_ascii=False)}


def said(text: str) -> dict:
    return {"role": "assistant", "content": text}


def names(files: list, sizes: bool = True) -> str:
    listed = [f"{f['file']} ({f['size']})" if sizes else f["file"] for f in files]
    return listed[0] if len(listed) == 1 else ", ".join(listed[:-1]) + " and " + listed[-1]


def n_files(n: int, what: str = "files") -> str:
    return answers._n(n, what)


def kinds_in(items: list) -> list:
    """The kind arguments that fit this folder (at least one file of it), with an extension or two."""
    out = []
    for kind in KIND_WORDS:
        wanted = answers.tool_filter({"kind": kind})
        if any(wanted.matches(p, s) for p, s in items):
            out.append(("kind", kind))
    exts = {p.suffix.lower() for p, _ in items}
    out += [("extension", e) for e in EXTENSIONS if e in exts]
    return out


def say_kind(r, arg) -> str:
    key, value = arg
    if key == "kind":
        return r.choice(KIND_WORDS[value])
    return r.choice([value[1:] + "s", value + " files", value[1:] + " files"])


def list_turns(args: dict, items: list, subfolders: list, place: str) -> tuple:
    """(tool call, tool result message, result) for list_files on the made-up folder."""
    result, _ = answers.list_result(args, items, [], subfolders, place, lambda p: str(p.parent))
    return call("list_files", **args), tool("list_files", result), result


def count_answer(result: dict, place: str) -> str:
    n, what = result["matching_files"], result["looked_for"]
    if n == 0:
        return f"There are no {what} in {place}."
    text = f"{place} has {n_files(n, what)} ({result['total_size']})."
    if 0 < n <= 3 and what != "files":
        text += " " + ("It is " if n == 1 else "They are ") + names(result["files"], sizes=False) + "."
    return text


def make(r, scenario: dict, others: list, qtype: str, index: int) -> dict:
    facts = scenario["facts"]
    place = facts["folder"]
    items, subfolders = items_of(facts), facts.get("subfolders", [])
    mention = folder_mention(r, place)
    kinds = kinds_in(items) or [("kind", "documents")]
    messages, last_folder = [], None

    if qtype == "follow_up":
        # the first question and its answer, as text only (the app keeps no tool calls in the history)
        first_args = {"folder": place}
        _, _, first = list_turns(first_args, items, subfolders, place)
        messages += [{"role": "user", "content": r.choice(QUESTIONS["count"][1]).replace("{f}", mention)},
                     said(count_answer(first, place))]
        last_folder = place
        kind = r.choice(kinds)
        word = say_kind(r, kind)
        choice = r.randrange(5)
        if choice == 0:
            user = r.choice([f"how many of them are {word}?", f"and how many {word}?", f"how many are {word}?"])
            c, t, res = list_turns({"folder": place, kind[0]: kind[1]}, items, subfolders, place)
            answer = count_answer(res, place)
        elif choice == 1:
            user = r.choice(["and the biggest one?", "which one is the biggest?", "what's the biggest of them?"])
            c, t, res = list_turns({"folder": place, "sort_by": "size", "order": "desc", "limit": 1},
                                   items, subfolders, place)
            answer = f"The biggest file in {place} is {names(res['files'])}."
        elif choice == 2:
            user = r.choice(["which is the newest?", "and the newest one?", "what did I get last?"])
            c, t, res = list_turns({"folder": place, "sort_by": "date", "order": "desc", "limit": 1},
                                   items, subfolders, place)
            answer = f"The newest file in {place} is {res['files'][0]['file']}, changed {res['files'][0]['modified']}."
        elif choice == 3:
            user = r.choice(["how many files are there now?", "how many files are there?", "and how big is it?"])
            if "big" in user:
                summary = answers.summary_result(place, items, [], _copies(items))
                c, t = call("folder_summary", folder=place), tool("folder_summary", summary)
                answer = f"The {n_files(summary['files'])} in {place} take {summary['total_size']}."
            else:
                c, t, res = list_turns({"folder": place}, items, subfolders, place)
                answer = count_answer(res, place)
        else:
            user = r.choice(["how many of them are older than a year?", "how many are from the last month?"])
            days = ("older_than_days", 365) if "year" in user else ("newer_than_days", 30)
            c, t, res = list_turns({"folder": place, days[0]: days[1]}, items, subfolders, place)
            answer = count_answer(res, place)
        messages += [{"role": "user", "content": user}, c, t, said(answer)]
        return record(scenario, index, qtype, messages, last_folder)

    if r.random() < 0.2 and qtype != "everywhere":  # an earlier folder in the prompt, but the user names this one
        last_folder = r.choice([f for f in MAIN_FOLDERS if f != place.split("/")[0]])
    template = r.choice(QUESTIONS[qtype][1])
    kind = r.choice(kinds)
    user = template.replace("{f}", mention).replace("{k}", say_kind(r, kind))

    if qtype == "count":
        c, t, res = list_turns({"folder": place}, items, subfolders, place)
        answer = count_answer(res, place)
    elif qtype == "count_kind":
        c, t, res = list_turns({"folder": place, kind[0]: kind[1]}, items, subfolders, place)
        answer = count_answer(res, place)
        if user.startswith("are there") and res["matching_files"]:
            answer = "Yes. " + answer
    elif qtype == "position":
        n = r.choice([3, 5])
        user = user.replace("{n}", str(n))
        which = next(w for w in ("biggest", "largest", "newest", "last", "oldest", "smallest") if w in user)
        sort_by = "size" if which in ("biggest", "largest", "smallest") else "date"
        order = "asc" if which in ("oldest", "smallest") else "desc"
        limit = n if "{n}" in template else 1
        c, t, res = list_turns({"folder": place, "sort_by": sort_by, "order": order, "limit": limit},
                               items, subfolders, place)
        word = {"largest": "biggest", "last": "newest"}.get(which, which)
        if limit == 1:
            f = res["files"][0]
            answer = f"The {word} file in {place} is {f['file']} ({f['size']}, changed {f['modified']})."
        else:
            answer = f"The {len(res['files'])} {word} files in {place} are {names(res['files'])}."
    elif qtype == "size":
        if "{k}" in template:
            c, t, res = list_turns({"folder": place, kind[0]: kind[1]}, items, subfolders, place)
            answer = f"The {n_files(res['matching_files'], res['looked_for'])} in {place} take {res['total_size']}." \
                if res["matching_files"] else f"There are no {res['looked_for']} in {place}."
        else:
            summary = answers.summary_result(place, items, [(s, []) for s in subfolders], _copies(items))
            c, t = call("folder_summary", folder=place), tool("folder_summary", summary)
            answer = f"The {n_files(summary['files'])} in {place} take {summary['total_size']}."
    elif qtype == "summary":
        summary = answers.summary_result(place, items, [(s, []) for s in subfolders], _copies(items))
        c, t = call("folder_summary", folder=place), tool("folder_summary", summary)
        kinds_said = ", ".join(answers._n(n, k) for k, n in summary["kinds"].items())
        answer = f"{place} has {n_files(summary['files'])}: {kinds_said}, {summary['total_size']} in all."
        if summary["exact_copies"]:
            answer += f" {summary['exact_copies']} of them are exact copies."
    elif qtype == "date":
        if "older than a year" in user:
            args = {"folder": place, "older_than_days": 365}
        elif "6 months" in user:
            args = {"folder": place, "older_than_days": 180}
        elif "last week" in user:
            args = {"folder": place, "newer_than_days": 7}
        else:
            args = {"folder": place, kind[0]: kind[1], "newer_than_days": 30}
        c, t, res = list_turns(args, items, subfolders, place)
        answer = count_answer(res, place)
    elif qtype == "everywhere":
        top = place.split("/")[0]
        groups = [(top, items)] + [(name, items_of(o["facts"])) for name, o in
                                   zip([f for f in MAIN_FOLDERS if f != top][:3], others)]
        overview = answers.overview_result(groups)
        c, t = call("folder_summary"), tool("folder_summary", overview)
        rows = sorted(overview["main_folders"], key=lambda x: -x["files"])
        if "most" in user:
            answer = f"{rows[0]['folder']} has the most files ({rows[0]['files']})."
        elif "space" in user:
            answer = f"Your main folders take {overview['total_size']}: " + \
                     ", ".join(f"{x['folder']} {x['size']}" for x in rows) + "."
        else:
            answer = f"You have {overview['total_files']} files in your main folders: " + \
                     ", ".join(f"{x['folder']} {x['files']}" for x in rows) + "."
    else:  # search
        query, found = _query(r, items)
        user = user.replace("{q}", query)
        args = {"query": query} if "{f}" not in template else {"query": query, "folder": place}
        where = place if "folder" in args else "your folders"
        text = (f"Found {len(found)} file{'s' if len(found) != 1 else ''} matching “{query}” in {where}."
                if found else f"No files matching “{query}” in {where}.")
        shown = [{"file": p.name, "folder": place, "size": answers.size_text(s["size_kb"]), "modified": s["modified"]}
                 for p, s in found[:10]]
        c, t = call("search_files", **args), tool("search_files", {"result": text, "files": shown})
        answer = (f"Yes, {names(shown[:3])} in {place}." if user.startswith("is there") else
                  f"I found {names(shown[:3])} in {place}.") if found else \
            f"I couldn't find anything matching “{query}”."
    messages += [{"role": "user", "content": user}, c, t, said(answer)]
    return record(scenario, index, qtype, messages, last_folder)


def _copies(items: list) -> int:
    return sum("duplicate_of" in s for _, s in items)


def _query(r, items: list) -> tuple:
    """A word from one file's name ('invoice', 'cv', 'turkcell') and the files whose name or text has it."""
    words = sorted({w for p, _ in items for w in _words(p.stem) if w not in GENERIC})
    query = r.choice(words) if words else "notes"
    found = [(p, s) for p, s in items if query in p.name.lower() or query in s.get("text_start", "").lower()]
    return query, found


def _words(stem: str) -> list:
    return [w for w in re.findall(r"[a-z]+", stem.lower()) if len(w) >= 3]


def record(scenario: dict, index: int, qtype: str, messages: list, last_folder) -> dict:
    return {"mode": "question",
            "meta": {"scenario_id": scenario["scenario_id"], "task_id": f"{scenario['scenario_id']}-q{index}",
                     "request_type": f"question_{qtype}", "last_folder": last_folder},
            "messages": [{"role": "system", "content": question_prompt(last_folder)}] + messages}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default=str(HERE / "tasks.jsonl"))
    ap.add_argument("--per-scenario", type=int, default=2)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", default=str(HERE / "questions.jsonl"))
    args = ap.parse_args()
    scenarios = {}
    for line in open(args.tasks, encoding="utf-8"):
        if line.strip():
            t = json.loads(line)
            scenarios.setdefault(t["scenario_id"], t)
    r = random.Random(args.seed)
    ids, types = sorted(scenarios), list(QUESTIONS)
    weights = [QUESTIONS[t][0] for t in types]
    n = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for sid in ids:
            others = [scenarios[o] for o in r.sample(ids, k=min(3, len(ids)))]
            for i, qtype in enumerate(r.choices(types, weights=weights, k=args.per_scenario)):
                out.write(json.dumps(make(r, scenarios[sid], others, qtype, i), ensure_ascii=False) + "\n")
                n += 1
    print(f"Wrote {n} question conversations from {len(ids)} folders to {args.out}")


if __name__ == "__main__":
    main()
