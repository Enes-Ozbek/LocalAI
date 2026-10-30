"""What app.py sends the page for the Now and After windows, requests in the background, and the page API's
answers to bad input, with a scripted model."""

import json
import threading
import time
import urllib.error
import urllib.request

import app as app_module
from conftest import reply


def finished(a) -> dict:
    for _ in range(100):
        if not a.status()["running"]:
            break
        time.sleep(0.05)
    return a.status()


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


def test_a_request_runs_in_the_background_and_a_second_one_waits(base, model, monkeypatch):
    go = threading.Event()
    model(reply("inspect_folder", folder="Downloads"), reply("propose_changes", changes=[]))
    a = app_module.App(base, model=None)
    real_run = a.run
    monkeypatch.setattr(a, "run", lambda text: (go.wait(5), real_run(text))[1])
    first = a.start("organise downloads")
    assert first["started"] == 1 and first["running"] and first["text"] == "organise downloads"
    second = a.start("find my cv")  # the page follows the running one instead of getting an error
    assert second["busy"] and second["id"] == 1 and second["text"] == "organise downloads"
    assert "organise downloads" in a.busy_text()
    go.set()
    done = finished(a)
    assert not done["running"] and done["result"]["kind"] == "proposal"
    assert a.start("find my cv")["started"] == 2


def test_a_crash_in_a_request_ends_it_with_the_error(base, monkeypatch):
    a = app_module.App(base, model=None)
    monkeypatch.setattr(a, "run", lambda text: 1 / 0)
    a.start("organise downloads")
    assert finished(a)["result"] == {"error": "ZeroDivisionError: division by zero"}
    assert a.start("again")["started"] == 2  # the lock was given back


def test_the_page_api_checks_the_token_and_answers_errors_as_json(base):
    a = app_module.App(base, model=None)
    server = app_module.ThreadingHTTPServer(("127.0.0.1", 0), app_module.handler(a, 0))
    port = server.server_address[1]
    server.RequestHandlerClass = app_module.handler(a, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}"

    def post(path, body=b"{}", token=app_module.TOKEN):
        req = urllib.request.Request(url + path, data=body, headers={"X-Token": token} if token else {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    try:
        assert post("/api/info", token=None)[0] == 403 and post("/api/info", token="wrong")[0] == 403
        assert post("/api/nothing")[0] == 403
        assert post("/api/info")[1]["version"] == app_module.VERSION
        assert "can't read" in post("/api/run", b"not json")[1]["error"]
        assert post("/api/run", b'{"text": "  "}')[1] == {"error": "Type a request first."}
        assert post("/api/run", b'{"text": 5}')[1]["error"].startswith("AttributeError")
        assert post("/api/apply", b'{"selected": [0]}')[1] == {"error": "There is no plan to apply. Send a request first."}
        assert post("/api/open", b'{"i": 3}')[1] == {"error": "No such search result."}
        assert post("/api/act", b'{"selected": [1], "action": "delete"}')[1]["error"].startswith("No files")
        assert post("/api/undo")[1] == {"message": "Nothing to undo."}
        with a.lock:  # a request is running: changes wait, the page can still ask how it is going
            assert post("/api/undo")[1]["busy"] and post("/api/apply")[1]["busy"]
            assert not post("/api/progress")[1]["running"]
        with urllib.request.urlopen(url + "/fonts/figtree-latin.woff2") as r:
            assert r.headers["Content-Type"] == "font/woff2"
        for bad in ("/fonts/../app.py", "/fonts/OFL.txt", "/app.py"):
            try:
                urllib.request.urlopen(url + bad)
                raise AssertionError(bad)
            except urllib.error.HTTPError as e:
                assert e.code == 404
    finally:
        server.shutdown()
