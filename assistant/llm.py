"""One place for the model settings, so switching models or runtimes is a one-line change.

Two ways to run the model, both on your GPU:
  llama-server  (default when models/assistant-r8.Q4_K_M.gguf and ..\\tools\\llama.cpp are there) our fine-tuned
                model through llama.cpp. LLM_GGUF picks another GGUF file.
  ollama        (otherwise, or set LLM_BACKEND=ollama) the stock qwen3.5 from `ollama pull`
Both return the same shape (response.message.content / .tool_calls), so the rest of the app doesn't care.

Nothing has to be running before the app opens: start() (called when the app opens) starts llama-server, or
`ollama serve` when Ollama isn't already running, and stop() ends whatever the app started when the app closes. A
server that was already running (one you started yourself, or Ollama's own app) is used as it is and left running.

embed() gives meaning vectors for search, from a small embedding model: llama-server with EMBED_GGUF (started like the
chat model, on its own port) when that file is there, otherwise Ollama's nomic-embed-text if Ollama runs and has it.
Without either, search still works by name, folder and text.
"""

import atexit
import json
import os
import shutil
import subprocess
import sys
import threading
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
_ollama = None  # the `ollama serve` this app started, if any
_lock = threading.Lock()  # the app starts the model when it opens, and a first request may want it at the same time
_state = {"loading": False, "ready": False, "error": ""}  # for the page: is the model loaded yet?
_on_close = None  # kept alive: Windows calls it when the terminal window closes
_stop_registered = False

EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
EMBED_GGUF = Path(os.environ.get("EMBED_GGUF", ROOT / "models" / "nomic-embed-text-v1.5.Q8_0.gguf"))
EMBED_SERVER = os.environ.get("EMBED_SERVER", "http://127.0.0.1:8081")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
_embed_server = None  # the embedding llama-server this app started, if any
_embed_lock = threading.Lock()
_embed_off_until = 0.0  # after a failure, search goes on without meanings for a minute instead of waiting each time

OPTIONS = {
    "temperature": 0,   # same input -> same answer; important for testing
    "num_ctx": 8192,    # enough for the tools + a folder of up to 40 files
}


def chat(messages, model=None, tools=None, format=None, options=None):
    options = {**OPTIONS, **(options or {})}
    if BACKEND == "llama-server":
        return _server_chat(messages, model or MODEL, tools, format, options)
    _ensure_ollama()
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


def _server_ready(server: str = None) -> bool:
    try:
        with _opener().open(f"{server or SERVER}/health", timeout=2) as resp:
            return resp.status == 200
    except OSError:  # not running yet, or 503 while the model loads
        return False


def start():
    """Loads the model now, when the app opens, so it is ready for the first request. A failure is kept for status()
    instead of raised: the app still opens, and the first request tries again and shows the reason."""
    try:
        _ensure_server() if BACKEND == "llama-server" else _ensure_ollama()
    except RuntimeError:  # the reason is in _state for the page
        pass


def status() -> dict:
    """{"loading", "ready", "error"}: whether the model is loading, loaded, or couldn't start (and why)."""
    return dict(_state)


def stop():
    """Ends the servers this app started, when the app closes, so nothing keeps running or holds the GPU."""
    for proc in (_server, _embed_server, _ollama):
        if proc is not None and proc.poll() is None:
            if os.name == "nt" and proc is _ollama:  # `ollama serve` runs the model in a child process: end both
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                proc.terminate()


def _ensure_server():
    """Starts llama-server with our GGUF on the GPU, unless one is already answering on SERVER. Starts it again if
    it was closed since (for example in Task Manager)."""
    def launch():
        global _server
        for path in (LLAMA_SERVER, GGUF):
            if not path.exists():
                raise RuntimeError(f"llama-server is not running at {SERVER} and can't be started: {path} not found")
        print(f"Loading {GGUF.name} with llama.cpp...", file=sys.stderr)
        _server = _launch([str(LLAMA_SERVER), "-m", str(GGUF), "--jinja", "-ngl", "99",
                           "-c", str(OPTIONS["num_ctx"]), "--port", str(urlparse(SERVER).port or 8080)],
                          "llama-server.log", "llama-server")
        return _server
    _ensure(lambda: _server, launch, _server_ready, 180, "llama-server", "llama-server.log")


def _ensure_ollama():
    """Uses Ollama if it is already running (its own app starts it with Windows unless that is turned off), and
    otherwise starts `ollama serve` for as long as this app runs."""
    def launch():
        global _ollama
        exe = _ollama_exe()
        if exe is None:
            raise RuntimeError(f"Ollama is not running at {OLLAMA} and ollama.exe wasn't found. Install Ollama from "
                               "ollama.com, or put the fine-tuned GGUF in models\\ and llama.cpp in ..\\tools\\llama.cpp.")
        print("Starting Ollama...", file=sys.stderr)
        _ollama = _launch([exe, "serve"], "ollama.log", "Ollama")
        return _ollama
    _ensure(lambda: _ollama, launch, _ollama_ready, 60, "Ollama", "ollama.log")


def _ensure(started, launch, ready, seconds: int, name: str, log_name: str):
    """The chat model's server: used if it answers, otherwise started (again, if ours has stopped) and waited for.
    _state follows along for the page."""
    with _lock:
        if ready():
            _state.update(loading=False, ready=True, error="")
            return
        try:
            proc = started()
            if proc is None or proc.poll() is not None:
                _state.update(loading=True, ready=False, error="")
                proc = launch()
            deadline = time.time() + seconds
            while not ready():
                if proc.poll() is not None:
                    raise RuntimeError(f"{name} stopped (exit code {proc.returncode}), see {ROOT / log_name}")
                if time.time() > deadline:
                    raise RuntimeError(f"{name} wasn't ready after {seconds} seconds, see {ROOT / log_name}")
                time.sleep(0.5)
        except RuntimeError as e:
            _state.update(loading=False, ready=False, error=str(e))
            raise
        _state.update(loading=False, ready=True, error="")


def _ollama_ready() -> bool:
    try:
        with _opener().open(f"{OLLAMA}/api/version", timeout=2) as resp:
            return resp.status == 200
    except OSError:
        return False


def _ollama_exe():
    found = os.environ.get("OLLAMA_EXE") or shutil.which("ollama")
    if found:
        return found
    if not os.environ.get("LOCALAPPDATA"):
        return None
    default = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Ollama" / "ollama.exe"  # where Ollama's installer puts it
    return str(default) if default.exists() else None


def _launch(cmd: list, log_name: str, name: str):
    """Starts a server in the background without a window. stop() ends it when the app closes."""
    global _stop_registered
    log = open(ROOT / log_name, "w", encoding="utf-8")
    try:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        log.close()
        raise RuntimeError(f"{name} couldn't be started: {e}") from e
    if not _stop_registered:
        atexit.register(stop)  # free the GPU when the app ends
        _stop_registered = True
    _stop_on_window_close()
    return proc


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
    with _embed_lock:
        if _server_ready(EMBED_SERVER):
            return
        if _embed_server is None or _embed_server.poll() is not None:
            _embed_server = _launch([str(LLAMA_SERVER), "-m", str(EMBED_GGUF), "--embedding", "-ngl", "99",
                                     "-c", "2048", "-b", "2048", "-ub", "2048",
                                     "--port", str(urlparse(EMBED_SERVER).port or 8081)],
                                    "llama-server-embed.log", "the embedding server")
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

    def on_close(event):
        stop()
        return False  # so Windows still ends the app as usual
    _on_close = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)(on_close)
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
