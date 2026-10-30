"""The request -> plan flow in assistant/agent.py, with scripted model replies, and Apply + Undo."""

from pathlib import Path

from assistant import agent, executor
from conftest import reply


def where(r, base):
    return [str(Path(s["dst"]).relative_to(base)).replace("\\", "/") for s in r.steps if s["op"] == "move"]


def test_move_to_another_folder_goes_to_that_folder(base, model):
    model(reply("inspect_folder", folder="Downloads"),
          reply("propose_changes", "Your CV.", changes=[{"file": "CV_Enes_2025.docx", "action": "move",
                                                         "to_folder": "Documents", "reason": "CV"}]))
    r = agent.run("move CV_Enes_2025.docx from downloads to documents", base=base)
    assert r.kind == "proposal" and where(r, base) == ["Documents/CV_Enes_2025.docx"]


def test_a_new_subfolder_stays_inside_the_folder(base, model):
    model(reply("inspect_folder", folder="Downloads"),
          reply("propose_changes", changes=[{"file": "scan_0012.pdf", "action": "move", "to_folder": "Bills",
                                             "reason": "bill"}]))
    r = agent.run("move scan_0012.pdf in downloads into a folder called Bills", base=base)
    assert where(r, base) == ["Downloads/Bills/scan_0012.pdf"]


def test_organizing_the_whole_folder_never_leaves_it(base, model):
    model(reply("inspect_folder", folder="Downloads"),
          reply("propose_changes", changes=[{"file": "lecture5.pdf", "action": "move", "to_folder": "Documents",
                                             "reason": "lecture"}]))
    r = agent.run("organize downloads and put the documents together", base=base)
    assert where(r, base) == ["Downloads/Documents/lecture5.pdf"]


def test_a_plan_before_looking_gets_one_retry(base, model):
    sent = model(reply("propose_changes", changes=[]),
                 reply("inspect_folder", folder="Downloads"),
                 reply("propose_changes", changes=[{"file": "budget.csv", "action": "move", "to_folder": "Documents",
                                                    "reason": "budget"}]))
    r = agent.run("move budget.csv from downloads to documents", base=base)
    assert where(r, base) == ["Documents/budget.csv"]
    assert sent[1][-1]["content"] == agent.LOOK_FIRST
    assert [m["role"] for m in sent[2]] == ["system", "user", "assistant", "tool"]  # the wrong try is gone


def test_changing_another_file_than_the_named_one_gets_one_retry(base, model):
    sent = model(reply("inspect_folder", folder="Downloads"),
                 reply("propose_changes", changes=[{"file": "IMG_2034 (1).jpg", "action": "delete",
                                                    "reason": "exact duplicate"}]),
                 reply("propose_changes", "Moving it.", changes=[{"file": "IMG_2034.jpg", "action": "move",
                                                                  "to_folder": "Pictures", "reason": "photo"}]))
    r = agent.run("put IMG_2034.jpg from downloads into pictures", base=base)
    assert where(r, base) == ["Pictures/IMG_2034.jpg"] and not r.blocked
    assert "only asked about IMG_2034.jpg" in sent[2][-1]["content"]


def test_the_newest_file_is_named_before_the_model_sees_the_request(base, model):
    sent = model(reply("inspect_folder", folder="Downloads"),
                 reply("propose_changes", changes=[{"file": "thesis_final_REAL.docx", "action": "delete",
                                                    "reason": "asked"}]))
    r = agent.run("delete the last file in downloads", base=base)
    assert r.text.startswith("The newest file in Downloads is thesis_final_REAL.docx.")
    assert sent[1][1]["content"] == "delete thesis_final_REAL.docx in downloads"
    assert [s["op"] for s in r.steps] == ["recycle"]


def test_system_folders_and_unnamed_folders(base, model):
    model(reply("inspect_folder", folder="Program Files"))
    assert agent.run("clean up Program Files", base=base).kind == "refuse"
    model(reply("inspect_folder", folder="Desktop"))
    assert agent.run("organize my downloads", base=base).kind == "ask"


def test_dont_change_anything_gives_no_plan(base, model):
    model(reply("inspect_folder", folder="Desktop"),
          reply("propose_changes", "Five files.", changes=[{"file": "notes.txt", "action": "delete", "reason": "x"}]))
    r = agent.run("what's on my desktop? don't change anything", base=base)
    assert r.kind == "explain" and not r.steps


def test_apply_and_undo_put_every_file_back(base, model):
    model(reply("inspect_folder", folder="Downloads"),
          reply("propose_changes", changes=[
              {"file": "CV_Enes_2025.docx", "action": "move", "to_folder": "Documents", "reason": "CV"},
              {"file": "report_v1.docx", "action": "rename", "new_name": "report_first_draft.docx", "reason": "x"}]))
    r = agent.run("move CV_Enes_2025.docx to documents and rename report_v1.docx in downloads", base=base)
    before = sorted(p.relative_to(base) for p in base.rglob("*") if p.is_file())
    executor.apply(r.steps)
    assert (base / "Documents" / "CV_Enes_2025.docx").exists()
    assert (base / "Downloads" / "report_first_draft.docx").exists()
    assert executor.undo_last() == "Moved 2 file(s) back."
    assert sorted(p.relative_to(base) for p in base.rglob("*") if p.is_file()) == before
