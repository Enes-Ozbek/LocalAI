"""Questions about files: the model picks the read-only tools (schema.READ_TOOLS), code counts and sorts, and
the Session remembers the conversation. Model replies are scripted."""

import json

import pytest

from assistant import agent, answers
from assistant.schema import QUESTION_PROMPT
from conftest import reply


@pytest.fixture
def by_model(monkeypatch):
    monkeypatch.setattr(agent, "QUESTION_MODE", "model")


def tool_result(sent_call) -> dict:
    """What the last tool told the model, in one call."""
    return json.loads(sent_call[-1]["content"])


def test_the_model_picks_a_tool_and_code_counts(base, model, by_model):
    sent = model(reply("list_files", folder="Downloads"), reply(text="Downloads has 19 files, and 9 more in New folder1."))
    r = agent.run("how many files are in downloads?", base=base)
    seen = tool_result(sent[1])
    assert seen["matching_files"] == 19 and "9 more" in seen["not_counted"]
    assert r.kind == "search" and r.text.startswith("Downloads has 19") and r.folder == base / "Downloads"
    assert sent[0][0]["content"] == QUESTION_PROMPT  # not the organizing prompt it was trained on


def test_the_biggest_file_comes_from_sorting_not_from_the_model(base, model, by_model):
    largest = max(p.stat().st_size for p in (base / "Archive").iterdir())
    sent = model(reply("list_files", folder="Archive", sort_by="size", order="desc", limit=1),
                 reply(text="That's the biggest file in Archive."))
    r = agent.run("what's the biggest file in archive", base=base)
    [shown] = tool_result(sent[1])["files"]
    assert (base / "Archive" / shown["file"]).stat().st_size == largest
    assert [m["name"] for m in r.matches] == [shown["file"]]


def test_filters_and_a_summary_of_every_main_folder(base, model, by_model):
    sent = model(reply("folder_summary"), reply("list_files", folder="Archive", extension="pdf"),
                 reply(text="Archive has 39 pdfs."))
    agent.run("how many files do I have, and how many pdfs are in archive?", base=base)
    rows = {row["folder"]: row["files"] for row in tool_result(sent[1])["main_folders"]}
    assert rows == {"Archive": 160, "Desktop": 4, "Documents": 0, "Downloads": 28, "Pictures": 0}
    assert tool_result(sent[2])["matching_files"] == 39


def test_search_is_a_tool_too(base, model, by_model):
    model(reply("search_files", query="cv"), reply(text="Your CVs are in Downloads."))
    r = agent.run("find my cv", base=base)
    assert r.kind == "search" and "CV_Enes_2025.docx" in [m["name"] for m in r.matches]


def test_a_wrong_folder_gets_an_error_the_model_can_fix(base, model, by_model):
    sent = model(reply("list_files", folder="Nowhere"), reply("list_files", folder="dowloads"),
                 reply(text="19 files."))
    agent.run("how many files are in my downloads", base=base)
    assert "Downloads" in tool_result(sent[1])["error"] and tool_result(sent[2])["matching_files"] == 19


def test_an_answer_without_looking_is_replaced_by_counting(base, model, by_model):
    model(reply(text="You have 42 files."))
    r = agent.run("how many files are there?", base=base)
    assert "192 files" in r.text and "42" not in r.text


def test_requests_that_change_files_still_go_the_trained_way(base):
    assert not agent.is_question("delete the biggest file in downloads")
    assert not agent.is_question("organize downloads")
    assert agent.is_question("is there a pdf about taxes?") and agent.is_question("the 3 newest files in archive")


def test_code_answers_when_the_model_is_off(base):
    assert answers.answer("how many files are in downloads", base)["text"].startswith("Downloads has 19 files")
    assert answers.answer("how big is my desktop", base)["text"].startswith("The 4 files in Desktop take")
    r = answers.answer("how many documents do I have", base)  # documents: files, not the Documents folder
    assert r["text"].startswith("Your main folders have 60 documents")
    assert answers.answer("the 3 newest files in archive", base)["kind"] == "search"


def test_a_folder_answer_finishes_the_waiting_request(base, model):
    sent = model(reply("ask_clarification", question=agent.ASK_FOLDER),
                 reply("inspect_folder", folder="Downloads"),
                 reply("propose_changes", changes=[{"file": "budget.csv", "action": "move", "to_folder": "Sheets",
                                                    "reason": "spreadsheet"}]))
    s = agent.Session(base)
    assert s.run("organize my files").kind == "ask"
    r = s.run("downloads")
    assert sent[1][1]["content"] == "organize my files in downloads" and r.kind == "proposal"


def test_follow_ups_reuse_the_folder_and_the_question(base, model):
    s = agent.Session(base)
    assert s.run("how many files are in downloads").text.startswith("Downloads has 19")
    assert s.run("and desktop?").text.startswith("Desktop has 4")
    sent = model(reply("inspect_folder", folder="Desktop"),
                 reply("propose_changes", changes=[{"file": "notes.txt", "action": "move", "to_folder": "Notes",
                                                    "reason": "notes"}]))
    s.run("organize it")
    assert sent[0][1]["content"] == "organize it in Desktop"
    assert s.run("how many files are there now?").text.startswith("Desktop has")


def test_the_model_sees_the_conversation_with_a_question(base, model, by_model):
    s = agent.Session(base)
    model(reply("list_files", folder="Archive"), reply(text="Archive has 160 files."))
    s.run("how many files are in archive")
    sent = model(reply("list_files", folder="Archive", kind="photos"), reply(text="78 of them are photos."))
    s.run("how many of them are photos?")
    assert [m["content"] for m in sent[0][1:3]] == ["how many files are in archive", "Archive has 160 files."]
    assert "The last answer was about the folder Archive" in sent[0][0]["content"]
