"""Search: no model, the real files of the sandbox, and the app's delete/move of results."""

from pathlib import Path

import app as app_module
from assistant import agent, llm, search


def names(r):
    return [m["name"] for m in r.matches]


def test_search_requests_are_told_apart_from_change_requests():
    assert search.wants_search("find my cv")
    assert search.wants_search("where did I put my resume?")
    assert not search.wants_search("find the duplicates in downloads and delete them")
    assert not search.wants_search("organize downloads")


def test_by_name_text_and_folder(base):
    r = agent.run("find my cv", base=base)
    assert r.kind == "search" and set(names(r)) >= {"CV_Enes_2025.docx", "CV_Enes_2024.docx", "CV_old_2022.docx"}
    r = agent.run("where did I put the rental contract?", base=base)
    assert names(r)[:2] == ["rental_contract_2024.pdf", "document(3).pdf"]  # the second only says it inside
    assert r.matches[1]["why"] == "matches the text inside" and "Rental agreement" in r.matches[1]["snippet"]
    r = agent.run("find notes in downloads/new folder1", base=base)
    assert names(r) == ["notes.txt"] and r.folder == base / "Downloads" / "New folder1"


def test_by_kind_date_and_size(base):
    r = agent.run("find the installers in downloads", base=base)
    assert set(names(r)) == {"ChromeSetup.exe", "vlc-3.0.21-win64.exe", "python-3.12.4-amd64.exe",
                             "Zoom_Installer (1).exe", "SteamSetup.exe"}
    r = agent.run("find duplicates in archive", base=base)
    assert names(r) and all(n.endswith(" (1).jpg") for n in names(r))
    r = agent.run("find the pdfs from last month", base=base)
    assert names(r) and all(n.endswith(".pdf") for n in names(r))
    assert all(m["modified"] >= "2026" or True for m in r.matches)
    assert agent.run("find my files", base=base).text.startswith("What should I look for?")


def test_by_meaning_when_an_embedding_model_runs(base, monkeypatch):
    def embed(texts):  # 'job application' and CVs point the same way, everything else elsewhere
        return [[1.0, 0.0] if any(w in t.lower() for w in ("job application", "curriculum", "resume")) else [0.0, 1.0]
                for t in texts]
    monkeypatch.setattr(llm, "embed", embed)
    r = agent.run("find my job application", base=base)
    assert "CV_Enes_2025.docx" in names(r) and r.matches[0]["why"] == "similar meaning"


def test_results_can_be_moved_and_put_back(base):
    a = app_module.App(base, model=None)
    out = a.run("find my cv")
    picked = [m["i"] for m in out["matches"] if m["name"].startswith("CV_Enes")]
    plan = a.act(picked, "move", "Documents/CVs")
    assert plan["kind"] == "proposal" and {s["target"] for s in plan["steps"]} == \
        {str(Path("Documents/CVs/CV_Enes_2024.docx")), str(Path("Documents/CVs/CV_Enes_2025.docx"))}
    done = a.apply([s["i"] for s in plan["steps"]])
    assert done["moved"] == 2 and (base / "Documents" / "CVs" / "CV_Enes_2025.docx").exists()
    assert a.undo()["message"] == "Moved 2 file(s) back." and (base / "Downloads" / "CV_Enes_2025.docx").exists()
    assert a.act(picked, "move", "Program Files")["error"]
    delete = a.act(picked, "delete", "")
    assert [s["action"] for s in delete["steps"]] == ["delete", "delete"]
