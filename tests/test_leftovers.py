"""Leftovers of uninstalled apps: the folder facts, the hard rules, the quarantine and Undo, with a scripted model.
The sandbox's fake PC (make_sandbox.APPS and installed.json) has Ollama, Docker, Origin and others uninstalled,
Discord running, Steam and VS Code installed, and game saves of a removed game."""

import os
import time
from pathlib import Path

import pytest

import app as app_module
import make_sandbox
from assistant import agent, executor, leftovers
from conftest import reply


def items(*rows):
    return reply("propose_cleanup", items=[{"folder": f, "verdict": v, "app": a, "reason": r} for f, v, a, r in rows])


@pytest.mark.parametrize("text,app", [
    ("find leftovers of apps I uninstalled", ""), ("clean up leftover files from uninstalled apps", ""),
    ("clean appdata", ""), ("show me leftovers", ""),
    ("clean up after Ollama", "Ollama"), ("what did docker leave behind?", "docker"),
    ("remove ollama leftovers", "ollama"), ("I uninstalled ollama, clean up its files", "ollama"),
    ("find leftover files from Docker Desktop on my pc", "Docker Desktop")])
def test_requests_for_leftovers_and_the_app_they_name(text, app):
    assert leftovers.wants_leftovers(text) and leftovers.named_app(text) == app


@pytest.mark.parametrize("text", ["organize downloads", "how many files are there?", "find my cv",
                                  "delete the last file in downloads"])
def test_other_requests_are_not_about_leftovers(text):
    assert not leftovers.wants_leftovers(text)


def test_names_match_installed_programs_but_not_look_alikes():
    assert leftovers.similarity("Docker", "Docker Desktop") >= 0.95
    assert leftovers.similarity("EpicGamesLauncher", "Epic Games Launcher") == 1.0
    assert leftovers.similarity("pip", "7-Zip 23.01 (x64)") < leftovers.SHOWN_MATCH
    assert leftovers.similarity("Origin", "EA app") < leftovers.SHOWN_MATCH


def test_the_scan_gives_one_sheet_per_app_folder_and_never_windows_folders(base):
    sheets, _ = leftovers.scan(base)
    by = {s["folder"]: s for s in sheets}
    expected = {f for f, _, what in make_sandbox.APPS if what != "never"}
    assert set(by) == expected  # AppData/Local/Microsoft and Temp are never looked at; Programs is looked into
    assert by["AppData/Local/Discord"]["used_by_running_program"]
    assert by["AppData/Roaming/discord"]["closest_installed"][0] == {"program": "Discord", "score": 1.0}
    assert by["AppData/Local/Docker"]["closest_installed"] == [] and by["AppData/Local/Docker"]["last_change_days"] >= 90
    assert by["AppData/Roaming/EldenRing"]["kinds"]["saves"] == 1 and leftovers.own_work(by["AppData/Roaming/EldenRing"]["w"])
    assert "w" not in leftovers.model_sheets(sheets)[0]  # code's own copy never reaches the model


def test_the_rules_hold_back_what_must_stay_and_untick_what_needs_a_look(base, model):
    model(items(("AppData/Local/Docker", "leftover", "Docker", "Docker Desktop is not installed"),
                ("Origin", "leftover", "Origin", "EA's old launcher"),  # only the last part: still found
                ("AppData/Local/Discord", "leftover", "Discord", "wrong on purpose"),
                ("AppData/Local/QuickNote", "leftover", "QuickNote", "not installed"),
                ("AppData/Roaming/EldenRing", "leftover", "Elden Ring", "the game is gone"),
                (".vscode", "leftover", "VS Code", "wrong on purpose"),
                ("AppData/Roaming/discord", "cache", "Discord", "browser cache"),
                ("AppData/Roaming/Wireshark", "cache", "Wireshark", "no cache in it"),
                ("AppData/Local/Microsoft", "leftover", "Windows", "never"),
                ("AppData/Local/Nothing", "leftover", "", "made up")))
    p = leftovers.plan("find leftovers of apps I uninstalled", base)
    steps = {s["folder"]: s for s in p["steps"]}
    blocked = {b["folder"]: b["blocked"] for b in p["blocked"]}
    assert steps["AppData/Local/Docker"]["checked"] and not steps["AppData/Local/Docker"]["contents_only"]
    assert steps["AppData/Local/Origin"]["checked"] and steps["AppData/Local/Origin"]["src"] == str(base / "AppData/Local/Origin")
    assert blocked["AppData/Local/Discord"] == "a running program uses it"
    assert blocked["AppData/Local/QuickNote"].startswith("changed 4 days ago")
    assert not steps["AppData/Roaming/EldenRing"]["checked"] and "your own files" in steps["AppData/Roaming/EldenRing"]["look"]
    assert not steps[".vscode"]["checked"] and "is installed" in steps[".vscode"]["look"]
    assert steps["AppData/Roaming/discord"]["contents_only"] and steps["AppData/Roaming/discord"]["checked"]
    assert "no cache" in blocked["AppData/Roaming/Wireshark"]
    assert blocked["AppData/Local/Nothing"] == "no such app folder in the scan"
    assert "AppData/Local/Microsoft" in blocked  # never scanned, so never found
    assert "2 leftovers" in p["text"] and "1 cache " in p["text"] and "2 folders to check yourself" in p["text"]


def test_naming_the_app_looks_only_at_its_folders_and_allows_recent_ones(base, model):
    sent = model(items(("AppData/Roaming/ollama app.exe", "leftover", "Ollama", "settings of the old tray app"),
                       ("AppData/Local/Ollama", "leftover", "Ollama", "server logs"),
                       (".ollama", "leftover", "Ollama", "downloaded models")))
    p = leftovers.plan("clean up after Ollama", base)
    facts = sent[0][-1]["content"]
    assert '"only_app": "Ollama"' in facts and "Docker" not in facts
    assert {s["folder"] for s in p["steps"] if s["checked"]} == {"AppData/Roaming/ollama app.exe", "AppData/Local/Ollama",
                                                               ".ollama"}  # changed 1 and 2 days ago: named, so allowed


def test_a_model_that_answers_without_the_tool_is_asked_once_more(base, model):
    sent = model(reply(text="These look like leftovers. Shall I remove them?"),
                 items(("AppData/Local/Origin", "leftover", "Origin", "not installed")))
    p = leftovers.plan("clean up after Origin", base)
    assert [s["folder"] for s in p["steps"]] == ["AppData/Local/Origin"] and len(sent) == 2
    assert sent[1][-1]["content"] == leftovers.GO_AHEAD


def test_cleanup_moves_to_quarantine_and_undo_puts_everything_back(base, model):
    model(items(("AppData/Local/Docker", "leftover", "Docker", "not installed"),
                ("AppData/Roaming/discord", "cache", "Discord", "cache")))
    a = app_module.App(base, model=None)
    out = a.run("find leftovers of apps I uninstalled")
    assert out["kind"] == "cleanup" and {s["verdict"] for s in out["steps"]} == {"leftover", "cache"}
    before = sorted(p.relative_to(base) for p in (base / "AppData").rglob("*"))
    done = a.apply([s["i"] for s in out["steps"]])
    assert done["cleanup"] and done["moved"] == 2 and not done["failed"]
    assert not (base / "AppData/Local/Docker").exists()
    assert sorted(p.name for p in (base / "AppData/Roaming/discord").iterdir()) == []  # emptied, the folder stays
    assert executor.quarantine_size() > 1_000_000
    assert a.undo()["message"].startswith("Moved 2 folder(s) back.")
    assert sorted(p.relative_to(base) for p in (base / "AppData").rglob("*")) == before


def test_empty_now_deletes_for_good_and_undo_says_so(base, model):
    model(items(("AppData/Local/Origin", "leftover", "Origin", "not installed")))
    a = app_module.App(base, model=None)
    out = a.run("clean up after Origin")
    a.apply([s["i"] for s in out["steps"]])
    assert executor.empty_quarantine(older_than_days=executor.QUARANTINE_DAYS) == (0, 0)  # too new
    runs, freed = executor.empty_quarantine()
    assert runs == 1 and freed > 70_000 and executor.quarantine_size() == 0
    assert not (base / "AppData/Local/Origin").exists()
    assert executor.undo_last() == "Nothing to undo."  # the emptied run is no longer offered


def test_a_folder_in_use_stays_and_the_rest_still_go(base, monkeypatch):
    real = os.rename

    def rename(src, dst):
        if Path(src).name == "Docker":
            raise PermissionError(32, "in use")
        return real(src, dst)
    monkeypatch.setattr(os, "rename", rename)
    steps = [{"op": "quarantine", "src": str(base / f), "folder": f, "contents_only": False}
             for f in ("AppData/Local/Docker", "AppData/Local/Origin")]
    log, failed = executor.apply_cleanup(steps)
    assert failed == [("AppData/Local/Docker", "a program is using it (close it and try again)")]
    assert (base / "AppData/Local/Docker").exists() and not (base / "AppData/Local/Origin").exists()


def test_leftover_requests_go_to_the_cleanup_before_questions(base, model):
    model(items())
    r = agent.run("what did ollama leave behind?", base)
    assert r.kind == "cleanup"


def test_old_quarantine_is_emptied_by_age(base, model):
    model(items(("AppData/Local/Origin", "leftover", "Origin", "not installed")))
    leftovers_steps = leftovers.plan("clean up after Origin", base)["steps"]
    executor.apply_cleanup(leftovers_steps)
    box = next(executor.QUARANTINE_DIR.iterdir())
    old = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() - 8 * 86400))
    box.rename(box.with_name(old))
    assert executor.empty_quarantine(older_than_days=executor.QUARANTINE_DAYS)[0] == 1


def test_training_conversations_match_what_the_app_sends(tmp_path):
    import json
    import random
    import make_leftovers
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "train"))
    import sft_data
    r = random.Random(1)
    recs = [make_leftovers.make_pc(r, i) for i in range(40)]
    assert any(x["meta"]["request_type"] == "cleanup_named" for x in recs)
    for rec in recs:
        [(prompt, target)] = sft_data.examples(rec)  # only the propose_cleanup turn is trained
        assert prompt[0]["content"] == leftovers.LEFTOVER_PROMPT and sft_data.tools_for(rec)[1]["function"]["name"] == \
            "propose_cleanup"
        facts = json.loads(prompt[-1]["content"])
        shown = {f["folder"]: f for f in facts["folders"]}
        assert len(shown) <= leftovers.PART and target["tool_calls"][0]["function"]["name"] == "propose_cleanup"
        for it in target["tool_calls"][0]["function"]["arguments"]["items"]:
            f = shown[it["folder"]]
            if it["verdict"] == "leftover":  # never a folder the facts tie to an installed or running program
                assert not f["used_by_running_program"]
                assert all(m["score"] < leftovers.OWNED for m in f["closest_installed"])
            if it["verdict"] == "cache":
                assert f["kinds"].get("cache_logs", 0) >= 2 * f["files"] / 3
