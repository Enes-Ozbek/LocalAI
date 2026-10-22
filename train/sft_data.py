"""Turns reviewed records into fine-tuning examples. Used by colab/2_finetune.ipynb.

Each assistant turn becomes its own example, cut exactly where the app asks the model:
  1. system + request                              -> inspect_folder / ask_clarification / refuse
  2. system + request + inspect call + folder facts -> explanation + propose_changes
Only the assistant turn is trained (the rest is masked), and the prompt is rendered with the same
"no thinking" generation prompt the app uses, so training and use see identical text.

    python train/sft_data.py dataset/train.jsonl     # prints how many examples a file gives
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant.schema import SYSTEM_PROMPT, TOOLS, tool_result  # noqa: E402

IGNORE = -100  # label value the loss skips


def normalize(rec: dict) -> list:
    """The record's messages with today's system prompt and tool-result format, so data drafted
    before a prompt change still matches what the app sends."""
    msgs = []
    for m in rec["messages"]:
        m = dict(m)
        if m["role"] == "system":
            m["content"] = SYSTEM_PROMPT
        elif m["role"] == "tool":
            facts = json.loads(m["content"])
            facts.pop("next_step", None)
            m["content"] = tool_result(facts)
            m.pop("tool_name", None)
        msgs.append(m)
    return msgs


def examples(rec: dict) -> list:
    """[(prompt messages, assistant message to learn)] for one record."""
    msgs = normalize(rec)
    return [(msgs[:i], m) for i, m in enumerate(msgs) if m["role"] == "assistant" and i >= 2]


def render(apply_template, prompt: list, target: dict) -> tuple:
    """(prompt text, full text) where full text = prompt text + the target turn.
    apply_template(messages, add_generation_prompt) -> str is the tokenizer's chat template."""
    prompt_text = apply_template(prompt, True)
    full = apply_template(prompt + [target], False)
    if full.startswith(prompt_text):
        return prompt_text, full
    # Some templates write the last assistant turn without the empty <think></think> block that the
    # no-thinking generation prompt has. Rebuild it as generation prompt + the turn's body.
    base = apply_template(prompt, False)
    header = "<|im_start|>assistant\n"
    if not full.startswith(base) or not full[len(base):].startswith(header):
        raise ValueError("chat template: can't line up prompt and answer")
    body = full[len(base) + len(header):]
    for empty in ("<think>\n\n</think>\n\n", "<think>\n</think>\n\n", "<think></think>"):
        if body.startswith(empty):
            body = body[len(empty):]
            break
    return prompt_text, prompt_text + body


def encode(tokenizer, apply_template, prompt: list, target: dict, max_len: int):
    """input_ids + labels (prompt masked), or None if the example is too long or doesn't line up."""
    try:
        prompt_text, full = render(apply_template, prompt, target)
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
        for prompt, target in examples(json.loads(line)):
            ex = encode(tokenizer, apply_template, prompt, target, max_len)
            if ex is None:
                skipped["too long or misaligned"] += 1
            else:
                out.append(ex)
    return out, skipped


if __name__ == "__main__":
    recs = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
    n = sum(len(examples(r)) for r in recs)
    print(f"{len(recs)} records -> {n} training examples ({len(TOOLS)} tools in every prompt)")
