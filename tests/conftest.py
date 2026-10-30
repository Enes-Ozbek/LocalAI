"""Shared test helpers. No model is needed: replies are scripted, everything else (scanner, safety rules,
executor, undo) is the real code, run on a fresh sandbox in a temporary folder.

    pip install pytest
    python -m pytest
"""

import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dataset"))
os.environ["LLM_BACKEND"] = "ollama"  # never start llama-server from a test

import make_sandbox  # noqa: E402
from assistant import agent, executor, llm, search  # noqa: E402


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    # Windows keeps temporary folders in AppData, which the safety rules protect, so the tests make theirs here
    if config.option.basetemp is None:
        config.option.basetemp = str(ROOT / ".pytest_tmp")


@pytest.fixture
def base(tmp_path, monkeypatch):
    """A fresh sandbox (Downloads, Desktop, Archive, Documents, Pictures), and an undo log and search index of its
    own. No embedding model: tests that need meanings script llm.embed themselves. Questions are answered by
    code: tests of the model's tool use set agent.QUESTION_MODE to "model"."""
    monkeypatch.chdir(tmp_path)
    make_sandbox.main()
    monkeypatch.setattr(executor, "UNDO_DIR", tmp_path / "undo")
    monkeypatch.setattr(search, "INDEX", tmp_path / "search.sqlite")
    monkeypatch.setattr(llm, "embed", lambda texts: None)
    monkeypatch.setattr(agent, "QUESTION_MODE", "code")  # questions without the model; test_questions scripts it
    yield (tmp_path / "sandbox").resolve()
    monkeypatch.undo()  # leave the folder before removing it (Windows can't remove the current folder)
    shutil.rmtree(tmp_path / "sandbox", ignore_errors=True)  # 2 MB per test


def reply(tool=None, text="", **args):
    """One model reply: some text and, optionally, one tool call."""
    calls = [NS(function=NS(name=tool, arguments=args))] if tool else None
    return NS(message=NS(role="assistant", content=text, tool_calls=calls))


@pytest.fixture
def model(monkeypatch):
    """model(reply1, reply2, ...) makes llm.chat answer with those replies in order. Returns the list of
    message lists the model was sent, one per call."""
    def script(*replies):
        sent, it = [], iter(replies)

        def chat(messages, **kw):
            sent.append([llm._to_openai(m) for m in messages])  # also checks they convert for llama-server
            return next(it)
        monkeypatch.setattr(llm, "chat", chat)
        return sent
    return script
