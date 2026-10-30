"""The hard rules in assistant/safety.py: they hold whatever the model proposes."""

import os
import time
from pathlib import Path

from assistant import safety
from assistant.scanner import resolve_folder, scan


def sheets(base, folder="Downloads"):
    return scan(resolve_folder(folder, base), folder)["files"]


def blocked_why(changes, files, user):
    return {b["file"]: b["blocked"] for b in safety.check_changes(changes, files, user)[1]}


def test_system_folders_are_refused_by_name():
    for name in ("Program Files", "program files (x86)", "Windows/System32", "AppData/Local", "programdata"):
        assert safety.names_system_folder(name), name
    assert not safety.names_system_folder("Downloads")


def test_home_folder_and_drive_root_are_protected():
    assert safety.is_protected(safety.HOME)
    assert safety.is_protected(Path(safety.HOME.anchor))


def test_wants_changes_reads_typos_and_no_change_requests():
    assert safety.wants_changes("organzie my downloads")
    assert safety.wants_changes("move budget.csv to documents")
    assert not safety.wants_changes("what's in downloads? don't change anything")
    assert not safety.wants_changes("just tell me what's on my desktop")


def test_names_folder_needs_the_folder_in_the_request():
    assert safety.names_folder("Downloads", "clean up my downlods")
    assert not safety.names_folder("Desktop", "clean up my downloads")


def test_dont_delete_is_respected(base):
    files = sheets(base)
    why = blocked_why([{"file": "download.tmp", "action": "delete", "reason": "temp"}], files,
                      "organize downloads but don't delete anything")
    assert why == {"download.tmp": "you asked not to delete anything"}


def test_recent_files_are_not_deleted_unless_named(base):
    files = sheets(base)
    delete = [{"file": "thesis_final_REAL.docx", "action": "delete", "reason": "old"}]
    assert "changed in the last" in blocked_why(delete, files, "clean up downloads")["thesis_final_REAL.docx"]
    assert blocked_why(delete, files, "delete thesis_final_REAL.docx in downloads") == {}


def test_false_duplicate_and_temporary_claims_are_blocked(base):
    files = sheets(base)
    why = blocked_why([{"file": "budget.csv", "action": "delete", "reason": "exact duplicate"},
                       {"file": "ChromeSetup.exe", "action": "delete", "reason": "corrupted file"}], files, "clean downloads")
    assert why["budget.csv"].startswith("not a duplicate")
    assert why["ChromeSetup.exe"].startswith("not a temporary")


def test_the_last_copy_of_a_file_is_kept(base):
    files = sheets(base)
    copies = [s["file"] for s in files if s["file"].startswith("IMG_2034")]
    assert len(copies) == 2
    allowed, blocked = safety.check_changes([{"file": f, "action": "delete", "reason": "duplicate"} for f in copies],
                                            files, "clean downloads")
    assert len(allowed) == 1 and blocked[0]["blocked"] == "this would delete every copy of this file"


def test_targets_and_new_names_stay_inside_the_folder(base):
    files = sheets(base)
    why = blocked_why([{"file": "budget.csv", "action": "move", "to_folder": "C:/Windows", "reason": "x"},
                       {"file": "lecture5.pdf", "action": "move", "to_folder": "../..", "reason": "x"},
                       {"file": "report_v1.docx", "action": "rename", "new_name": "report.pdf", "reason": "x"},
                       {"file": "report_final.docx", "action": "rename", "new_name": "../x.docx", "reason": "x"}],
                      files, "organize downloads")
    assert set(why.values()) == {"target must be a subfolder of this folder", "new name changes the file extension",
                                 "new name must be a plain file name"}


def test_a_request_about_named_files_changes_only_those(base):
    files = sheets(base)
    why = blocked_why([{"file": "budget.csv", "action": "move", "to_folder": "Money", "reason": "asked"},
                       {"file": "download.tmp", "action": "delete", "reason": "temp"}], files,
                      "move budget.csv in downloads to a folder called Money")
    assert why == {"download.tmp": "you only asked about budget.csv"}
    assert safety.asked_files("move report_final to documents", [s["file"] for s in files]) == {"report_final.docx"}
    assert safety.asked_files("clean my downloads", [s["file"] for s in files]) == set()


def test_the_newest_file_is_picked_by_modified_time(base):
    folder = base / "Downloads"
    names = [p.name for p in folder.iterdir() if p.is_file()]
    now = time.time()
    os.utime(folder / "budget.csv", (now, now))
    assert safety.file_by_position("delete the last file in downloads", folder, names) == ("budget.csv", "newest")
    biggest = max(names, key=lambda n: (folder / n).stat().st_size)
    assert safety.file_by_position("delete the biggest file in downloads", folder, names) == (biggest, "biggest")
    assert safety.fill_in("delete the last file in downloads", "budget.csv") == "delete budget.csv in downloads"
