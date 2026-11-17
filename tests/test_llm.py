"""The model runs only while the app does: started when the app opens (nothing has to start with Windows), started
again if it was closed, and stopped when the app closes. A small fake server stands in for llama-server and Ollama."""

import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from assistant import llm

FAKE = Path(__file__).resolve().parent / "fake_model_server.py"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def launched(tmp_path, monkeypatch):
    """Points llm at fake llama-server and ollama programs in tmp_path; returns the commands the app launched."""
    for name in ("llama-server.exe", "model.gguf", "ollama.exe"):
        (tmp_path / name).write_text("fake")
    monkeypatch.setattr(llm, "ROOT", tmp_path)  # the server logs go here
    monkeypatch.setattr(llm, "LLAMA_SERVER", tmp_path / "llama-server.exe")
    monkeypatch.setattr(llm, "GGUF", tmp_path / "model.gguf")
    monkeypatch.setattr(llm, "SERVER", f"http://127.0.0.1:{free_port()}")
    monkeypatch.setattr(llm, "OLLAMA", f"http://127.0.0.1:{free_port()}")
    monkeypatch.setenv("OLLAMA_HOST", llm.OLLAMA)
    monkeypatch.setenv("OLLAMA_EXE", str(tmp_path / "ollama.exe"))
    monkeypatch.setattr(llm, "BACKEND", "llama-server")
    monkeypatch.setattr(llm, "_server", None)
    monkeypatch.setattr(llm, "_ollama", None)
    monkeypatch.setattr(llm, "_state", {"loading": False, "ready": False, "error": ""})
    fakes = {str(tmp_path / "llama-server.exe"), str(tmp_path / "ollama.exe")}
    commands, real = [], subprocess.Popen

    def popen(cmd, **kw):
        if cmd[0] not in fakes:  # taskkill, for example
            return real(cmd, **kw)
        commands.append(cmd)
        return real([sys.executable, str(FAKE), *cmd[1:]], **kw)
    monkeypatch.setattr(subprocess, "Popen", popen)
    yield commands
    started = [p for p in (llm._server, llm._ollama) if p is not None]
    llm.stop()
    for p in started:
        p.wait(10)


def test_the_model_starts_when_the_app_opens_and_only_once(launched):
    first_request = threading.Thread(target=llm.start)  # a request sent while the app is still loading the model
    first_request.start()
    llm.start()
    first_request.join()
    assert len(launched) == 1 and launched[0][1:3] == ["-m", str(llm.GGUF)]
    assert llm.status() == {"loading": False, "ready": True, "error": ""} and llm._server_ready()


def test_a_model_closed_since_is_started_again(launched):
    llm.start()
    llm._server.kill()  # for example in Task Manager
    llm._server.wait(10)
    llm._ensure_server()
    assert len(launched) == 2 and llm._server_ready()


def test_closing_the_app_stops_the_model(launched):
    llm.start()
    proc = llm._server
    llm.stop()
    proc.wait(10)
    assert not llm._server_ready()


def test_a_model_server_already_running_is_used_and_left_running(launched):
    port = llm.SERVER.rsplit(":", 1)[1]
    mine = subprocess.Popen([sys.executable, str(FAKE), "--port", port])
    try:
        for _ in range(100):
            if llm._server_ready():
                break
            time.sleep(0.1)
        llm.start()
        llm.stop()
        assert launched == [] and llm.status()["ready"] and mine.poll() is None
    finally:
        mine.kill()
        mine.wait(10)


def test_ollama_is_started_by_the_app_when_it_is_not_running(launched, monkeypatch):
    monkeypatch.setattr(llm, "BACKEND", "ollama")
    llm.start()
    assert [c[1:] for c in launched] == [["serve"]] and llm._ollama_ready() and llm.status()["ready"]
    proc = llm._ollama
    llm.stop()  # on Windows with taskkill /T, so the model process Ollama starts ends too
    proc.wait(10)
    assert not llm._ollama_ready()


def test_a_model_that_cant_start_is_shown_not_raised(launched, monkeypatch):
    monkeypatch.setattr(llm, "LLAMA_SERVER", llm.ROOT / "missing" / "llama-server.exe")
    llm.start()  # the app still opens
    st = llm.status()
    assert not st["ready"] and not st["loading"] and "not found" in st["error"]
    with pytest.raises(RuntimeError, match="not found"):  # and the request says why
        llm._ensure_server()


def test_a_model_server_that_exits_at_once_is_reported(launched, monkeypatch):
    monkeypatch.setenv("FAKE_SERVER_EXIT", "3")
    llm.start()
    assert "stopped (exit code 3)" in llm.status()["error"] and "llama-server.log" in llm.status()["error"]
