"""The app window's server (app.py): what the page gets to draw the folder now and after, and who may load it."""

import http.client
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import app as app_module
from conftest import reply


def test_a_plan_shows_the_folder_now_and_where_each_file_goes(base, model):
    folder = base / "Downloads" / "New folder1"
    (folder / "desktop.ini").write_text("[.ShellClassInfo]")  # system files stay out of the picture, as for the model
    (folder / ".hidden").write_text("x")
    model(reply("inspect_folder", folder="Downloads/New folder1"),
          reply("propose_changes", "Schoolwork, a bill and a copy of a photo.", changes=[
              {"file": "Lecture_07.pdf", "action": "move", "to_folder": "Schoolwork", "reason": "lecture notes"},
              {"file": "scan_0031.pdf", "action": "move", "to_folder": "Bills", "new_name": "Turkcell_2026-09.pdf",
               "reason": "phone bill"},
              {"file": "IMG_4411 (1).jpg", "action": "delete", "reason": "exact copy"}]))
    out = app_module.App(base, model=None).run("organize downloads/new folder1")
    assert out["kind"] == "proposal"
    steps = {s["name"]: s for s in out["steps"]}
    lecture, bill, copy = steps["Lecture_07.pdf"], steps["scan_0031.pdf"], steps["IMG_4411 (1).jpg"]
    assert lecture["to"] == ["Schoolwork"] and lecture["to_new"] == [True] and lecture["here"] and not lecture["bin"]
    assert bill["to"] == ["Bills"] and bill["new_name"] == "Turkcell_2026-09.pdf" and bill["out"] == ""
    assert copy["bin"] and "to" not in copy and copy["from"] == str(Path("Downloads/New folder1"))
    view = out["view"]
    assert view["name"] == "New folder1" and view["exists"] and not view["picked"] and view["more"] == 0
    names = [e["name"] for e in view["entries"]]
    assert len(names) == 9 and "desktop.ini" not in names and ".hidden" not in names
    assert names == sorted(names, key=str.lower)
    sheet = next(e for e in view["entries"] if e["name"] == "Lecture_07.pdf")
    assert not sheet["dir"] and sheet["size_kb"] > 0 and re.fullmatch(r"\d{4}-\d\d-\d\d", sheet["modified"])


def test_a_move_out_of_the_folder_says_where_it_goes(base, model):
    model(reply("inspect_folder", folder="Downloads"),
          reply("propose_changes", "Your CV.", changes=[{"file": "CV_Enes_2025.docx", "action": "move",
                                                         "to_folder": "Documents", "reason": "CV"}]))
    out = app_module.App(base, model=None).run("move CV_Enes_2025.docx from downloads to documents")
    (step,) = out["steps"]
    assert step["to"] is None and step["out"] == "Documents" and step["new_name"] == "CV_Enes_2025.docx"
    assert out["view"]["entries"][0] == {"name": "New folder1", "dir": True, "items": 9}  # subfolders first


def test_search_results_go_to_the_folder_the_page_shows(base):
    a = app_module.App(base, model=None)
    found = a.run("find my cv")
    picked = [m["i"] for m in found["matches"] if m["name"].startswith("CV_Enes")]
    move = a.act(picked, "move", "Documents/CVs")
    assert move["view"]["picked"] and move["view"]["name"] == "CVs" and not move["view"]["exists"]
    assert move["view"]["entries"] == []
    assert all(s["to"] == [] and not s["here"] and s["from"] == "Downloads" for s in move["steps"])
    assert a.act(picked, "delete", "")["view"] is None  # deletes of search results come from many folders


def test_an_explanation_shows_the_folder_and_an_answer_does_not(base, model):
    model(reply("inspect_folder", folder="Desktop"), reply(text="Two drafts of a proposal, a screenshot and a to-do list."))
    a = app_module.App(base, model=None)
    out = a.run("what's on my desktop? don't change anything")
    assert out["kind"] == "explain" and [e["name"] for e in out["view"]["entries"]] == [
        "Capstone_proposal_v2.docx", "Capstone_proposal_v3.docx", "Screenshot 2026-10-19 221503.png", "todo.txt"]
    answer = a.run("how many files are there?")
    assert answer["question"] and answer["view"] is None


def test_the_page_and_its_font_are_served_to_this_pc_only(base):
    a = app_module.App(base, model=None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = server.server_address[1]
    server.RequestHandlerClass = app_module.handler(a, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def send(method, path, host=f"127.0.0.1:{port}"):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request(method, path, body="{}" if method == "POST" else None, headers={"Host": host})
        r = c.getresponse()
        out = r.status, r.getheader("Content-Type"), r.read()
        c.close()
        return out
    try:
        status, kind, font = send("GET", "/fonts/figtree-latin.woff2")
        assert status == 200 and kind == "font/woff2" and font[:4] == b"wOF2"
        assert send("GET", "/fonts/figtree-latin-ext.woff2")[0] == 200
        assert send("GET", "/fonts/figtree-latin.woff2", host="example.com")[0] == 404
        assert send("GET", "/fonts/OFL.txt")[0] == 404 and send("GET", "/fonts/../app.py")[0] == 404
        status, _, page = send("GET", "/")
        assert status == 200 and app_module.TOKEN.encode() in page and b"__TOKEN__" not in page
        assert b"googleapis" not in page and b"gstatic" not in page  # the page loads nothing from the internet
        assert send("POST", "/api/info")[0] == 403  # without the page's token
    finally:
        server.shutdown()
        server.server_close()
