"""Folders with more files than the model sees at once (scanner.MAX_FILES), and requests about a kind of file."""

import json
import os
import time
from datetime import datetime

from assistant import agent, filters, scanner
from conftest import reply


def seen(sent_call) -> list:
    """The files the model was shown in one call."""
    return [s["file"] for s in json.loads(sent_call[-1]["content"])["files"]]


def test_the_filter_reads_kinds_dates_and_sizes_but_not_folder_names():
    now = datetime(2026, 10, 30)
    f = filters.parse("delete photos older than 2 years in archive", ("Archive",), now)
    assert f.kinds == {"images"} and f.older_days == 730 and f.picks_files()
    assert filters.parse("delete all the .tmp files in downloads").exts == {".tmp"}
    f = filters.parse("move all pdfs from downloads to documents", ("Downloads", "Documents"))
    assert f.exts == {".pdf"} and not f.kinds  # 'to documents' is where they go, not a kind of file
    assert filters.parse("organize my pictures", ("Pictures",)).empty()
    f = filters.parse("find videos bigger than 1.5 GB from 2024", (), now)
    assert f.kinds == {"videos"} and f.min_kb == 1.5 * 1024 * 1024 and f.year == 2024
    f = filters.parse("put the files with IMG in the name into a folder called \"Phone\"")
    assert f.name_has == ["img"]  # "Phone" is the target folder, not part of a name
    assert filters.parse("where are the screenshots from last week", (), now).newer_days == 14
    assert filters.parse("what's in archive? don't change anything", here=("Archive",)).empty()


def test_a_big_folder_goes_to_the_model_in_parts(base, model):
    files = [p.name for p in scanner.files_in(base / "Archive")]
    assert len(files) > 3 * scanner.MAX_FILES
    parts = [files[i:i + scanner.MAX_FILES] for i in range(0, len(files), scanner.MAX_FILES)]
    sent = model(reply("inspect_folder", folder="Archive"),
                 *[reply("propose_changes", f"Part with {p[0]}.", changes=[{"file": p[0], "action": "move",
                                                                          "to_folder": "Sorted", "reason": "x"}])
                   for p in parts])
    told = []
    r = agent.run("organize archive", base=base, progress=told.append)
    assert r.kind == "proposal" and len(sent) == 1 + len(parts)
    assert [seen(call) for call in sent[1:]] == parts
    # the folder a part made shows up as a subfolder in the next parts, so they put files in the same place
    assert "Sorted" not in json.loads(sent[1][-1]["content"])["subfolders"]
    assert "Sorted" in json.loads(sent[2][-1]["content"])["subfolders"]
    assert sorted(os.path.basename(s["src"]) for s in r.steps) == sorted(p[0] for p in parts)
    assert told[1].startswith(f"Looking at part 2 of {len(parts)}")
    assert r.text.startswith(f"Archive has {len(files)} files.") and "Part 2: Part with" in r.text


def test_a_bulk_delete_shows_the_model_only_those_files(base, model):
    folder = base / "Archive"
    tmp = sorted(p.name for p in scanner.files_in(folder) if p.suffix == ".tmp")
    now = time.time()
    os.utime(folder / tmp[0], (now, now))  # changed today: still deleted, the user asked for all of them
    sent = model(reply("inspect_folder", folder="Archive"),
                 reply("propose_changes", changes=[{"file": n, "action": "delete", "reason": "temporary"} for n in tmp]))
    r = agent.run("delete all the .tmp files in archive", base=base)
    assert seen(sent[1]) == tmp
    assert [s["op"] for s in r.steps] == ["recycle"] * len(tmp) and not r.blocked
    assert f"{len(tmp)} of them are .tmp files" in r.text


def test_organizing_by_kind_keeps_the_14_day_rule(base, model):
    folder = base / "Archive"
    shot = sorted(p.name for p in scanner.files_in(folder) if p.name.startswith("Screenshot"))[0]
    now = time.time()
    os.utime(folder / shot, (now, now))
    model(reply("inspect_folder", folder="Archive"),
          reply("propose_changes", changes=[{"file": shot, "action": "delete", "reason": "old screenshot"}]))
    r = agent.run("clean up the screenshots in archive", base=base)
    assert not r.steps and r.blocked[0]["blocked"] == "changed in the last 14 days"


def test_a_named_file_in_a_big_folder_is_shown_alone(base, model):
    sent = model(reply("inspect_folder", folder="Archive"),
                 reply("propose_changes", changes=[{"file": "CV_old_2022.docx", "action": "delete", "reason": "old"}]))
    r = agent.run("delete CV_old_2022.docx in archive", base=base)
    assert seen(sent[1]) == ["CV_old_2022.docx"] and len(r.steps) == 1


def test_nothing_of_that_kind_needs_no_plan(base, model):
    sent = model(reply("inspect_folder", folder="Archive"))
    r = agent.run("delete all the .iso files in archive", base=base)
    assert r.kind == "explain" and "none of them are .iso files" in r.text and len(sent) == 1


def test_explaining_a_big_folder_gives_an_overview(base, model):
    sent = model(reply("inspect_folder", folder="Archive"), reply(text="Mostly photos and lecture notes."))
    r = agent.run("what's in my archive folder? don't change anything", base=base)
    assert r.kind == "explain" and len(seen(sent[1])) == scanner.MAX_FILES
    assert "photos" in r.text and "are exact copies" in r.text and r.text.endswith("Mostly photos and lecture notes.")
