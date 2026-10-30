"""Turns reviewed records into fine-tuning examples. Used by colab/finetune_colab.ipynb.

Each assistant turn becomes its own example, cut exactly where the app asks the model:
  1. system + request                              -> inspect_folder / ask_clarification / refuse
  2. system + request + inspect call + folder facts -> explanation + propose_changes
Question records (dataset/make_questions.py, "mode": "question") use the question prompt and the read-only
tools instead, and only the turns after the last request are trained (the earlier answers of a follow-up
are context, as in the app):
  3. question prompt + [earlier request and answer] + question -> list_files / folder_summary / search_files
  4. ... + the tool call + its result                          -> the answer
Only the assistant turn is trained (the rest is masked), and the prompt is rendered with the same
"no thinking" generation prompt the app uses, so training and use see identical text.

    python train/sft_data.py dataset/train.jsonl     # prints how many examples a file gives
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant.schema import READ_TOOLS, SYSTEM_PROMPT, TOOLS, question_prompt, tool_result  # noqa: E402

IGNORE = -100  # label value the loss skips
# The key order llama.cpp makes the model use for each change (schema order). The teacher wrote keys
# in its own order, and the first fine-tuned model learned that order instead.
KEY_ORDER = {k: i for i, k in enumerate(next(t for t in TOOLS if t["function"]["name"] == "propose_changes")
                                         ["function"]["parameters"]["properties"]["changes"]["items"]["properties"])}


def is_question(rec: dict) -> bool:
    return rec.get("mode") == "question"


def tools_for(rec: dict) -> list:
    """The tools the model sees with this record: the read-only ones for a question."""
    return READ_TOOLS if is_question(rec) else TOOLS


def normalize(rec: dict) -> list:
    """The record's messages with today's system prompt, tool-result format and key order, so data
    drafted before a change still matches what the app sends and what llama.cpp lets the model write."""
    msgs = []
    for m in rec["messages"]:
        m = dict(m)
        if m["role"] == "system":
            m["content"] = question_prompt(rec.get("meta", {}).get("last_folder")) if is_question(rec) \
                else SYSTEM_PROMPT
        elif m["role"] == "tool" and is_question(rec):
            m.pop("tool_name", None)
        elif m["role"] == "tool":
            facts = json.loads(m["content"])
            facts.pop("next_step", None)
            m["content"] = tool_result(facts)
            m.pop("tool_name", None)
        elif m.get("tool_calls"):
            m["tool_calls"] = [_ordered(tc) for tc in m["tool_calls"]]
        msgs.append(m)
    return msgs


def _ordered(tc: dict) -> dict:
    args = tc["function"]["arguments"]
    if tc["function"]["name"] != "propose_changes" or not isinstance(args.get("changes"), list):
        return tc
    changes = [dict(sorted(c.items(), key=lambda kv: KEY_ORDER.get(kv[0], len(KEY_ORDER))))
               if isinstance(c, dict) else c for c in args["changes"]]
    return {**tc, "function": {**tc["function"], "arguments": {**args, "changes": changes}}}


def examples(rec: dict) -> list:
    """[(prompt messages, assistant message to learn)] for one record."""
    msgs = normalize(rec)
    first = 2
    if is_question(rec):  # a follow-up's earlier answer is context, not something to learn
        first = max(i for i, m in enumerate(msgs) if m["role"] == "user") + 1
    return [(msgs[:i], m) for i, m in enumerate(msgs) if m["role"] == "assistant" and i >= first]


def render(apply_template, prompt: list, target: dict, tools: list = TOOLS) -> tuple:
    """(prompt text, full text) where full text = prompt text + the target turn.
    apply_template(messages, add_generation_prompt, tools) -> str is the tokenizer's chat template."""
    prompt_text = apply_template(prompt, True, tools)
    full = apply_template(prompt + [target], False, tools)
    if full.startswith(prompt_text):
        return prompt_text, full
    # Some templates write the last assistant turn without the empty <think></think> block that the
    # no-thinking generation prompt has. Rebuild it as generation prompt + the turn's body.
    base = apply_template(prompt, False, tools)
    header = "<|im_start|>assistant\n"
    if not full.startswith(base) or not full[len(base):].startswith(header):
        raise ValueError("chat template: can't line up prompt and answer")
    body = full[len(base) + len(header):]
    for empty in ("<think>\n\n</think>\n\n", "<think>\n</think>\n\n", "<think></think>"):
        if body.startswith(empty):
            body = body[len(empty):]
            break
    return prompt_text, prompt_text + body


def encode(tokenizer, apply_template, prompt: list, target: dict, max_len: int, tools: list = TOOLS):
    """input_ids + labels (prompt masked), or None if the example is too long or doesn't line up."""
    try:
        prompt_text, full = render(apply_template, prompt, target, tools)
    except ValueError:
        return None
    p_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
    ids = tokenizer(full, add_special_tokens=False)["input_ids"]
    if ids[:len(p_ids)] != p_ids or len(ids) > max_len or len(ids) == len(p_ids):
        return None
    return {"input_ids": ids, "labels": [IGNORE] * len(p_ids) + ids[len(p_ids):]}


def build(path, tokenizer, apply_template, max_len: int = 6144) -> tuple:
    """All examples of a .jsonl file, plus counts of what was skipped."""
    out, skipped = [], {"too long or misaligned": 0}
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        rec = json.loads(line)
        for prompt, target in examples(rec):
            ex = encode(tokenizer, apply_template, prompt, target, max_len, tools_for(rec))
            if ex is None:
                skipped["too long or misaligned"] += 1
            else:
                out.append(ex)
    return out, skipped


if __name__ == "__main__":
    recs = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
    n = sum(len(examples(r)) for r in recs)
    q = sum(is_question(r) for r in recs)
    print(f"{len(recs)} records ({q} questions) -> {n} training examples "
          f"({len(TOOLS)} tools in an organizing prompt, {len(READ_TOOLS)} in a question prompt)")
