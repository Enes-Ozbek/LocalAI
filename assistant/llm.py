"""One place for the model settings, so switching models or runtimes is a one-line change.

Two ways to run the model, both on your GPU:
  llama-server  (default when models/assistant-r8.Q4_K_M.gguf and ..\\tools\\llama.cpp are there) our fine-tuned
                model through llama.cpp. The app starts the server itself and stops it when the app ends; a server
                you started yourself on LLM_SERVER is used as it is. LLM_GGUF picks another GGUF file.
  ollama        (otherwise, or set LLM_BACKEND=ollama) the stock qwen3.5 from `ollama pull`
Both return the same shape (response.message.content / .tool_calls), so the rest of the app doesn't care.

embed() gives meaning vectors for search, from a small embedding model: llama-server with EMBED_GGUF (started like the
chat model, on its own port) when that file is there, otherwise Ollama's nomic-embed-text if Ollama runs and has it.
Without either, search still works by name, folder and text.
"""

import atexit
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

# The Ollama model, used when the fine-tuned GGUF isn't there. To try another one: set MODEL=qwen3.5:9b.
MODEL = os.environ.get("MODEL", "qwen3.5:4b-q4_K_M")
SERVER = os.environ.get("LLM_SERVER", "http://127.0.0.1:8080")
ROOT = Path(__file__).resolve().parent.parent
GGUF = Path(os.environ.get("LLM_GGUF", ROOT / "models" / "assistant-r8.Q4_K_M.gguf"))
LLAMA_SERVER = Path(os.environ.get("LLAMA_SERVER", ROOT.parent / "tools" / "llama.cpp" / "bin" / "llama-server.exe"))
BACKEND = os.environ.get("LLM_BACKEND") or ("llama-server" if GGUF.exists() and LLAMA_SERVER.exists() else "ollama")
_server = None  # the llama-server this app started, if any
_on_close = None  # kept alive: Windows calls it when the terminal window closes

EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
EMBED_GGUF = Path(os.environ.get("EMBED_GGUF", ROOT / "models" / "nomic-embed-text-v1.5.Q8_0.gguf"))
EMBED_SERVER = os.environ.get("EMBED_SERVER", "http://127.0.0.1:8081")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
_embed_server = None  # the embedding llama-server this app started, if any
_embed_off_until = 0.0  # after a failure, search goes on without meanings for a minute instead of waiting each time

OPTIONS = {
    "temperature": 0,   # same input -> same answer; important for testing
    "num_ctx": 8192,    # enough for the tools + a folder of up to 40 files
}


def chat(messages, model=None, tools=None, format=None, options=None):
    options = {**OPTIONS, **(options or {})}
    if BACKEND == "llama-server":
        return _server_chat(messages, model or MODEL, tools, format, options)
    import ollama
    return ollama.chat(
        model=model or MODEL,
        messages=messages,
        tools=tools,
        format=format,
        think=False,        # no "thinking" text: much faster, not needed here
        options=options,
        keep_alive="30m",   # keep the model loaded on the GPU between requests
    )


def label(model=None) -> str:
    """What answers: our GGUF file through llama-server, otherwise the Ollama model."""
    return GGUF.stem if BACKEND == "llama-server" else (model or MODEL)


def _server_ready(server: str = SERVER) -> bool:
    try:
        with _opener().open(f"{server}/health", timeout=2) as resp:
            return resp.status == 200
    except OSError:  # not running yet, or 503 while the model loads
        return False


def _ensure_server():
    """Starts llama-server with our GGUF on the GPU the first time it's needed."""
    global _server
    if _server_ready():
        return
    if _server is None:
        for path in (LLAMA_SERVER, GGUF):
            if not path.exists():
                raise RuntimeError(f"llama-server is not running at {SERVER} and can't be started: {path} not found")
        log = open(ROOT / "llama-server.log", "w", encoding="utf-8")
        _server = subprocess.Popen([str(LLAMA_SERVER), "-m", str(GGUF), "--jinja", "-ngl", "99",
                                    "-c", str(OPTIONS["num_ctx"]), "--port", str(urlparse(SERVER).port or 8080)],
                                   stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        atexit.register(_server.terminate)  # free the GPU when the app ends
        _stop_on_window_close()
        print(f"Loading {GGUF.name} with llama.cpp...", file=sys.stderr)
    deadline = time.time() + 180
    while not _server_ready():
        if _server.poll() is not None:
            raise RuntimeError(f"llama-server stopped (exit code {_server.returncode}), see {ROOT / 'llama-server.log'}")
        if time.time() > deadline:
            raise RuntimeError(f"llama-server wasn't ready after 3 minutes, see {ROOT / 'llama-server.log'}")
        time.sleep(1)


def embed(texts: list):
    """One vector per text, or None when no embedding model is running. nomic-embed-text wants
    'search_query: ' or 'search_document: ' in front of each text."""
    global _embed_off_until
    if not texts or time.time() < _embed_off_until:
        return None
    try:
        if EMBED_GGUF.exists() and LLAMA_SERVER.exists():
            _ensure_embed_server()
            out = _post(f"{EMBED_SERVER}/v1/embeddings", {"model": EMBED_MODEL, "input": texts})
            return [d["embedding"] for d in sorted(out["data"], key=lambda d: d["index"])]
        out = _post(f"{OLLAMA}/api/embed", {"model": EMBED_MODEL, "input": texts}, timeout=120)
        return out["embeddings"]
    except (OSError, RuntimeError, KeyError, ValueError):
        _embed_off_until = time.time() + 60
        return None


def _post(url: str, body: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with _opener().open(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _ensure_embed_server():
    global _embed_server
    if _server_ready(EMBED_SERVER):
        return
    if _embed_server is None:
        log = open(ROOT / "llama-server-embed.log", "w", encoding="utf-8")
        _embed_server = subprocess.Popen([str(LLAMA_SERVER), "-m", str(EMBED_GGUF), "--embedding", "-ngl", "99",
                                          "-c", "2048", "-b", "2048", "-ub", "2048",
                                          "--port", str(urlparse(EMBED_SERVER).port or 8081)],
                                         stdout=log, stderr=subprocess.STDOUT,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        atexit.register(_embed_server.terminate)
        _stop_on_window_close()
    deadline = time.time() + 60
    while not _server_ready(EMBED_SERVER):
        if _embed_server.poll() is not None or time.time() > deadline:
            raise RuntimeError("the embedding server didn't start, see llama-server-embed.log")
        time.sleep(0.5)


def _stop_on_window_close():
    """Closing the terminal window ends Python without running atexit, which would leave llama-server
    holding the GPU. Windows reports the close to a console handler first, so stop the server there too."""
    global _on_close
    if os.name != "nt" or _on_close is not None:
        return
    import ctypes

    def stop(event):
        for s in (_server, _embed_server):
            if s:
                s.terminate()
        return False  # so Windows still ends the app as usual
    _on_close = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)(stop)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_on_close, True)


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost: never via a proxy


def _server_chat(messages, model, tools, format, options):
    _ensure_server()
    body = {"model": model, "messages": [_to_openai(m) for m in messages],
            "temperature": options["temperature"],
            "chat_template_kwargs": {"enable_thinking": False}}
    if tools:
        body["tools"] = tools
    if format:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": format}}
    req = urllib.request.Request(f"{SERVER}/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with _opener().open(req, timeout=600) as resp:
        msg = json.loads(resp.read())["choices"][0]["message"]
    calls = [SimpleNamespace(function=SimpleNamespace(name=c["function"]["name"],
                                                      arguments=_arguments(c["function"].get("arguments"))))
             for c in msg.get("tool_calls") or []]
    return SimpleNamespace(message=SimpleNamespace(role="assistant", content=msg.get("content") or "",
                                                   tool_calls=calls or None))


def _arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _to_openai(m) -> dict:
    """Our messages (dicts, or replies from an earlier turn) -> the OpenAI chat format llama-server reads."""
    if not isinstance(m, dict):
        m = {"role": m.role, "content": m.content,
             "tool_calls": [{"function": {"name": c.function.name, "arguments": dict(c.function.arguments)}}
                            for c in (m.tool_calls or [])]}
    out = {"role": m["role"], "content": m.get("content") or ""}
    if m.get("tool_calls"):
        out["tool_calls"] = [{"id": f"call_{i}", "type": "function",
                              "function": {"name": c["function"]["name"],
                                           "arguments": json.dumps(c["function"]["arguments"], ensure_ascii=False)}}
                             for i, c in enumerate(m["tool_calls"])]
    if m["role"] == "tool":
        out["tool_call_id"] = "call_0"
    return out
