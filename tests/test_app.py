"""What app.py sends the page for the Now and After windows, with a scripted model."""

import app as app_module
from conftest import reply


def test_a_plan_says_where_each_file_ends_up_in_the_folder(base, model):
    model(reply("inspect_folder", folder="Downloads/New folder1"),
          reply("propose_changes", "Notes, a bill and an installer.", changes=[
              {"file": "Lecture_07.pdf", "action": "move", "to_folder": "Schoolwork", "reason": "lecture notes"},
              {"file": "scan_0031.pdf", "action": "move", "to_folder": "Bills", "new_name": "Turkcell_2026-09.pdf",
               "reason": "phone bill"},
              {"file": "hw2_solution.pdf", "action": "rename", "new_name": "MATH201_hw2.pdf", "reason": "homework"},
              {"file": "SteamSetup.exe", "action": "delete", "reason": "old installer"}]))
    out = app_module.App(base, model=None).run("organise downloads/new folder1")
    assert out["kind"] == "proposal" and not out["picked"]
    assert out["here"]["exists"] and "notes.txt" in out["here"]["files"] and out["here"]["folders"] == []
    where = {s["name"]: (s["from_here"], s["to_dir"], s["new_name"]) for s in out["steps"]}
    assert where == {"Lecture_07.pdf": (True, "Schoolwork", "Lecture_07.pdf"),
                     "scan_0031.pdf": (True, "Bills", "Turkcell_2026-09.pdf"),
                     "hw2_solution.pdf": (True, "", "MATH201_hw2.pdf"),
                     "SteamSetup.exe": (True, None, "SteamSetup.exe")}


def test_a_move_to_another_folder_leaves_the_folder(base, model):
    model(reply("inspect_folder", folder="Downloads"),
          reply("propose_changes", changes=[{"file": "CV_Enes_2025.docx", "action": "move", "to_folder": "Documents",
                                             "reason": "CV"}]))
    out = app_module.App(base, model=None).run("move CV_Enes_2025.docx from downloads to documents")
    [step] = out["steps"]
    assert step["to_dir"] is None and step["from_here"] and "New folder1" in out["here"]["folders"]


def test_picked_search_results_move_into_the_folder_shown(base):
    a = app_module.App(base, model=None)
    out = a.run("find my cv")
    plan = a.act([m["i"] for m in out["matches"] if m["name"].startswith("CV_Enes")], "move", "Documents/CVs")
    assert plan["picked"] and plan["here"] == {"files": [], "folders": [], "exists": False}
    assert {(s["from_here"], s["to_dir"]) for s in plan["steps"]} == {(False, "")}


def test_the_font_files_are_there():
    assert all(p.is_file() and p.stat().st_size > 1000 for p in app_module.FONTS.values())
