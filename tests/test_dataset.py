"""The training data pipeline: generated requests, the checker, the repair of teacher answers and the counts."""

import json
import random
from pathlib import Path

import make_scenarios
import stats
from validate import check, drop_bad_changes

DATA = Path(__file__).resolve().parent.parent / "dataset"


def records(name):
    return [json.loads(line) for line in open(DATA / name, encoding="utf-8") if line.strip()]


def test_hand_written_and_test_records_pass_the_checks():
    for name in ("seed_examples.jsonl", "test.jsonl"):
        assert [check(r) for r in records(name)] == [[]] * len(records(name)), name


def tasks(n):
    r = random.Random(7)
    out = []
    for i in range(n):
        sc = make_scenarios.make_scenario(r, f"s{i:04}")
        out += [{**sc, **req} for req in make_scenarios.make_requests(r, sc, 4)]
    return out


def test_more_folders_keep_the_first_requests_the_same():
    assert tasks(20) == tasks(30)[:80]


def test_one_file_requests_name_a_file_of_that_folder():
    for t in tasks(60):
        if t["request_type"] in ("one_file", "move_elsewhere"):
            names = [s["file"] for s in t["facts"]["files"]]
            assert any(n in t["user"] or Path(n).stem in t["user"] for n in names), t["user"]


def organize_record(changes, text="Some files."):
    rec = next(r for r in records("seed_examples.jsonl") if r["meta"]["task_id"] == "seed-1")
    rec = json.loads(json.dumps(rec))
    rec["messages"][-1]["content"] = text
    rec["messages"][-1]["tool_calls"][0]["function"]["arguments"]["changes"] = changes
    return rec


def test_a_bad_delete_is_dropped_and_the_good_changes_are_kept():
    files = [s["file"] for s in json.loads(organize_record([])["messages"][3]["content"])["files"]]
    good = {"file": files[0], "action": "move", "to_folder": "Course PDFs", "reason": "lecture"}
    photo = next(f for f in files if f.lower().endswith(".jpg") and "(1)" not in f)
    bad = {"file": photo, "action": "delete", "reason": "old photo"}
    rec = organize_record([good, bad])
    assert check(rec)
    fixed, dropped = drop_bad_changes(rec)
    assert dropped == [photo] and not check(fixed)
    assert fixed["messages"][-1]["tool_calls"][0]["function"]["arguments"]["changes"] == [good]
    # not kept when the explanation talks about the dropped file, or nothing would be left
    assert drop_bad_changes(organize_record([good, bad], f"I'll delete {photo}."))[0] is None
    assert drop_bad_changes(organize_record([bad]))[0] is None


def test_the_counts(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records("seed_examples.jsonl")), encoding="utf-8")
    c = stats.count(path)
    assert c["conversations"] == 7 and c["examples"] == 12
    assert 0 < c["file decisions"] < 34  # the explain-only request's files are not decisions


def test_question_conversations_use_the_tools_and_answer_from_their_results(tmp_path):
    import make_questions
    from assistant.schema import QUESTION_TOOL_NAMES
    from train import sft_data
    scenarios = {}
    for t in tasks(30):
        scenarios.setdefault(t["scenario_id"], t)
    r, recs = random.Random(3), []
    for i, sc in enumerate(scenarios.values()):
        for qtype in make_questions.QUESTIONS:
            recs.append(make_questions.make(r, sc, list(scenarios.values())[:3], qtype, i))
    for rec in recs:
        msgs = rec["messages"]
        call, result, answer = msgs[-3], json.loads(msgs[-2]["content"]), msgs[-1]["content"]
        assert call["tool_calls"][0]["function"]["name"] in QUESTION_TOOL_NAMES and answer
        if rec["meta"]["request_type"] in ("question_count", "question_count_kind") and result["matching_files"]:
            assert str(result["matching_files"]) in answer  # the number comes from the tool, not made up
        learned = sft_data.examples(rec)
        assert [m.get("tool_calls") is not None for _, m in learned] == [True, False]  # a follow-up's first answer isn't
    # the question prompt and tools go with question records only
    seen = []
    def template(messages, gen, tools):
        seen.append({t["function"]["name"] for t in tools})
        return "".join(f"<|im_start|>{m['role']}\n{m.get('content') or ''}" for m in messages) + \
            ("<|im_start|>assistant\n" if gen else "")
    sft_data.render(template, *sft_data.examples(recs[0])[0], sft_data.tools_for(recs[0]))
    assert seen[0] == QUESTION_TOOL_NAMES
    path = tmp_path / "q.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in recs), encoding="utf-8")
    c = stats.count(path)
    assert c["questions"] == len(recs) and not c.get("file decisions")
