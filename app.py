"""The assistant in a window: a small page served on this PC only, opened in your browser.

    python app.py                    # works on your real folders (nothing changes until you press Apply)
    python app.py --base sandbox     # 'Downloads' means sandbox/Downloads: safe for testing

Same flow as cli.py: type a request, read the explanation, and see the folder as it is now next to how it will look
after the changes. Untick what you don't want, press Apply. 'Undo last run' reverses the last applied changes.
Press Ctrl+C here to quit.
Search requests ('find my CV') list files; the ones you tick can then be deleted or moved the same way.
"""

import argparse
import json
import os
import secrets
import socket
import subprocess
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from assistant import agent, executor, llm, safety, scanner

PAGE = Path(__file__).resolve().parent / "assistant" / "app.html"
FONTS = Path(__file__).resolve().parent / "assistant" / "fonts"
FILES = {f"/fonts/{name}": FONTS / name for name in ("figtree-latin.woff2", "figtree-latin-ext.woff2")}  # the page's
# font comes from here, not from the internet: the page loads nothing from outside this PC
TOKEN = secrets.token_urlsafe(16)  # only the page this app served can call the API
LISTED = 1000  # the page shows at most this many items of a folder
EXAMPLES = [
    ("check files in my downloads/new folder1 and tell me what those are then organise them",
     "Explains the files, then proposes a plan"),
    ("organize downloads but don't delete anything", "Moves and renames only, no deletes"),
    ("what's on my desktop? don't change anything", "Only an explanation, no plan"),
    ("delete all the .tmp files in archive", "A bulk job in a big folder: only those files go to the model"),
    ("find my cv", "Search by name, words inside or meaning"),
    ("how many files are there?", "A question: the model decides where to look, code counts exactly"),
]


class App:
    def __init__(self, base: Path, model: str):
        self.base, self.model = base, model
        self.last = None  # the latest proposal, so Apply runs exactly what was shown
        self.found = []   # the latest search results: the page refers to them by number only
        self.progress = ""
        self.session = agent.Session(base, model)  # remembers the conversation: 'how many files are there now?'

    def info(self) -> dict:
        return {"model": llm.label(self.model), "backend": llm.BACKEND, "base": str(self.base),
                "sandbox": self.base.name.lower() == "sandbox",
                "examples": [{"text": t, "hint": h} for t, h in EXAMPLES]}

    def run(self, text: str) -> dict:
        self.progress = ""
        r = self.session.run(text, progress=self._progress)
        self.last = r if r.kind == "proposal" else None
        self.found = r.matches if r.kind == "search" else []
        return self._result(r) | {"matches": [{"i": i, **{k: m[k] for k in ("name", "where", "size_kb", "modified",
                                                                         "why", "snippet")}}
                                              for i, m in enumerate(self.found)]}

    def _progress(self, text: str):
        self.progress = text

    def act(self, selected: list, action: str, to: str) -> dict:
        """A plan for the search results the user ticked: delete them, or move them into one folder."""
        files = [Path(self.found[i]["path"]) for i in sorted(set(selected))
                 if isinstance(i, int) and 0 <= i < len(self.found)]
        files = [p for p in files if p.is_file() and not safety.is_protected(p.parent)]
        if not files:
            return {"error": "No files are selected (or they are gone)."}
        if action == "delete":
            steps = [{"op": "recycle", "src": str(p), "reason": "you picked it in the search results"} for p in files]
            text, folder = f"Delete {len(files)} file(s) you picked: they go to the Recycle Bin.", self.base
        elif action == "move":
            to = (to or "").strip()
            folder = scanner.resolve_folder(to, self.base) if to else None
            if folder is None:
                return {"error": "Type the folder to move them to, for example Documents/CVs."}
            if safety.is_protected(folder) or safety.names_system_folder(to) or folder.is_file():
                return {"error": f"{to} is a protected folder or a file, so I won't move anything there."}
            steps = executor.move_steps(files, folder, f"you picked it in the search results to move to {to}")
            if not steps:
                return {"error": f"The files are already in {to}."}
            new = "" if folder.is_dir() else " (a new folder)"
            text = f"Move {len(steps)} file(s) you picked to {self._show(folder)}{new}."
        else:
            return {"error": f"Unknown action {action!r}."}
        r = agent.Result("proposal", text, folder=folder, steps=steps)
        self.last = r
        return self._result(r, by_path=True)

    def open(self, i) -> dict:
        """Shows one search result in Explorer, selected."""
        if not isinstance(i, int) or not 0 <= i < len(self.found):
            return {"error": "No such search result."}
        path = Path(self.found[i]["path"])
        if not path.exists():
            return {"error": f"{path.name} isn't there any more."}
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            webbrowser.open(path.parent.as_uri())
        return {"ok": True}

    def _result(self, r, by_path: bool = False) -> dict:
        question = bool(r.tools or r.by_code)
        # the folder as it is now, for the page's Now and After windows; a delete of search results needs none
        listed = r.folder and not question and r.kind in ("proposal", "explain") and \
            not (by_path and all(s["op"] == "recycle" for s in r.steps))
        return {"kind": r.kind, "text": r.text, "seconds": r.seconds, "question": question,
                "folder": self._show(r.folder) if r.folder else "",
                "steps": [self._step(i, s, r.folder, self.base if by_path else None) for i, s in enumerate(r.steps)],
                "blocked": [{"file": b.get("file", ""), "action": b.get("action", ""), "why": b["blocked"]}
                            for b in r.blocked],
                "view": self._view(r.folder, picked=by_path) if listed else None}

    def _view(self, folder: Path, picked: bool = False) -> dict:
        """What the folder holds now, as the model sees it (no hidden or system files): subfolders first, then
        files, by name. picked: the plan is for search results, which come from many folders."""
        entries = []
        if folder.is_dir():
            for name in scanner.subfolders_of(folder):
                try:
                    items = sum(1 for _ in (folder / name).iterdir())
                except OSError:  # no permission to look inside
                    items = None
                entries.append({"name": name, "dir": True, "items": items})
            for p in scanner.files_in(folder):
                try:
                    st = p.stat()
                except OSError:
                    continue
                entries.append({"name": p.name, "dir": False, "size_kb": round(st.st_size / 1024, 1),
                                "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")})
        return {"name": folder.name or str(folder), "path": self._show(folder), "exists": folder.is_dir(),
                "picked": picked, "entries": entries[:LISTED], "more": max(0, len(entries) - LISTED)}

    def apply(self, selected: list) -> dict:
        if self.last is None:
            return {"error": "There is no plan to apply. Send a request first."}
        chosen = set(selected)  # row numbers only: the steps themselves come from the plan kept here
        steps = [s for i, s in enumerate(self.last.steps) if i in chosen]
        if not steps:
            return {"error": "No changes are selected."}
        folder, self.last = self.last.folder, None  # a plan is applied at most once
        log = executor.apply(steps)
        moved = sum(s["op"] == "move" for s in steps)
        return {"folder": self._show(folder), "moved": moved, "recycled": len(steps) - moved, "log": str(log)}

    def undo(self) -> dict:
        return {"message": executor.undo_last()}

    def _show(self, folder: Path) -> str:
        try:
            return str(folder.relative_to(self.base)) if folder != self.base else folder.name
        except ValueError:
            return str(folder)

    def _step(self, i: int, s: dict, folder: Path, base: Path = None) -> dict:
        """base: show where each file comes from (search results come from many folders).
        The page draws the folder after the change from: name, from (its folder now), here (it is directly in
        `folder`), bin, and for a move: to (the subfolders of `folder` it goes into, [] for `folder` itself,
        None when it leaves `folder`), to_new (which of those don't exist yet), new_name, and out (the folder
        it goes to when it leaves `folder`)."""
        src = Path(s["src"])
        name = str(src.relative_to(base)) if base and src.is_relative_to(base) else str(src) if base else src.name
        where = {"name": src.name, "from": self._show(src.parent), "here": src.parent == folder}
        if s["op"] == "recycle":
            return {"i": i, "action": "delete", "file": name, "target": "Recycle Bin", "reason": s["reason"],
                    **where, "bin": True}
        dst = Path(s["dst"])
        renamed = dst.name != src.name
        action = "rename" if dst.parent == src.parent else ("move+rename" if renamed else "move")
        if base:
            target = dst.relative_to(base) if dst.is_relative_to(base) else dst
        else:
            target = dst.relative_to(folder) if dst.is_relative_to(folder) else dst  # full path when it leaves the folder
        inside = dst.parent.is_relative_to(folder)
        to = list(dst.parent.relative_to(folder).parts) if inside else None
        return {"i": i, "action": action, "file": name, "target": str(target), "reason": s["reason"], **where,
                "bin": False, "to": to, "new_name": dst.name, "out": "" if inside else self._show(dst.parent),
                "to_new": [not folder.joinpath(*to[:k + 1]).exists() for k in range(len(to))] if inside else []}


def handler(app: App, port: int):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    busy = threading.Lock()
    routes = {"/api/info": lambda body: app.info(), "/api/run": lambda body: app.run(body.get("text", "").strip()),
              "/api/apply": lambda body: app.apply(body.get("selected", [])), "/api/undo": lambda body: app.undo(),
              "/api/act": lambda body: app.act(body.get("selected", []), body.get("action", ""), body.get("to", "")),
              "/api/open": lambda body: app.open(body.get("i")),
              "/api/progress": lambda body: {"text": app.progress}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Host") in hosts and self.path in FILES:
                return self._send(200, FILES[self.path].read_bytes(), "font/woff2")
            if self.headers.get("Host") not in hosts or self.path != "/":
                return self._send(404, b"Not found", "text/plain")
            page = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self):
            # Other websites can't send this header without the token, so they can't reach the API.
            if self.headers.get("Host") not in hosts or self.headers.get("X-Token") != TOKEN \
                    or self.path not in routes:
                return self._send(403, b"Forbidden", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path in ("/api/info", "/api/progress"):  # answered while a request is running
                out = routes[self.path](body)
            elif not busy.acquire(blocking=False):  # one model request or file change at a time
                out = {"error": "Still working on the previous request. Wait for it to finish."}
            else:
                try:
                    out = routes[self.path](body)
                except Exception as e:  # show the error in the page instead of a broken connection
                    out = {"error": f"{type(e).__name__}: {e}"}
                finally:
                    busy.release()
            self._send(200, json.dumps(out, ensure_ascii=False).encode("utf-8"), "application/json")

        def _send(self, code, data, kind):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):  # keep the terminal quiet
            pass

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(Path.home()), help="where folder names are looked up")
    ap.add_argument("--model", default=llm.MODEL)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="don't open the page automatically")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}/"
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", args.port)) == 0:  # started twice: show the one that is already running
            print(f"The app is already running: {url}")
            if not args.no_browser:
                webbrowser.open(url)
            return
    app = App(Path(args.base).resolve(), args.model)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(app, args.port))
    print(f"Model: {llm.label(args.model)}   Folders under: {app.base}\nOpen {url}   (Ctrl+C to quit)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
