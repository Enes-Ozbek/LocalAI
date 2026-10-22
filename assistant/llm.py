"""One place for the model settings, so switching models or runtimes is a one-line change.

Two ways to run the model, both on your GPU:
  ollama        (default) the stock qwen3.5 from `ollama pull`
  llama-server  llama.cpp's server, for a GGUF file such as our fine-tuned model:
                    llama-server -m assistant-4b-q4_k_m.gguf --jinja -ngl 99 -c 8192 --port 8080
                then set LLM_BACKEND=llama-server
Both return the same shape (response.message.content / .tool_calls), so the rest of the app doesn't care.
"""

import json
import os
import urllib.request
from types import SimpleNamespace

# Start with the 4B. To try another model: set MODEL=qwen3.5:9b (or change this line).
MODEL = os.environ.get("MODEL", "qwen3.5:4b-q4_K_M")
BACKEND = os.environ.get("LLM_BACKEND", "ollama")
SERVER = os.environ.get("LLM_SERVER", "http://127.0.0.1:8080")

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


def _server_chat(messages, model, tools, format, options):
    body = {"model": model, "messages": [_to_openai(m) for m in messages],
            "temperature": options["temperature"],
            "chat_template_kwargs": {"enable_thinking": False}}
    if tools:
        body["tools"] = tools
    if format:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": format}}
    req = urllib.request.Request(f"{SERVER}/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost: never via a proxy
    with opener.open(req, timeout=600) as resp:
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
