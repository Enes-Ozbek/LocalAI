# v2 round 4 on RX 5600 XT: qwen3.5:4b-q4_K_M

Date: 2026-10-22. Folder: `C:\Users\kerim\Projects\local-assistant`. Everything ran on a fresh `sandbox\`; no real folders were touched.

## 1. Patch check

- Before applying, I restored `assistant/agent.py` to the round-3 version (hash `a0d4c85e447448d8`). I had tried my own fix for (d) in between; it is kept in `fix_round3_string_changes.diff`, see below.
- I converted README.md, assistant/agent.py and assistant/llm.py to LF. README had 75 CRLF lines, agent.py 108, and llm.py was already LF.
- `git apply --unidiff-zero fixes_round4.diff` applied cleanly to all 4 files.
- `python train/sft_data.py dataset/seed_examples.jsonl` printed "7 records -> 12 training examples (4 tools in every prompt)".

| File | Hash | OK |
|---|---|---|
| assistant/llm.py | a647a3d23ef4b1c0 | yes |
| assistant/agent.py | 7336642aab87da82 | yes |
| train/sft_data.py | b36f2d29d817449f | yes |
| README.md | 5d7cae5f35f5da97 | yes |

## 2. Requests a and d again (Ollama)

Full output: `results/run_ad_round4_ollama.txt`.

| # | Result | Seconds | Total |
|---|---|---|---|
| a | proposal, 6 moves + 1 delete (same plan as round 3), retry used | 1.29 (prompt cache), 16.52, 15.02 | 33.1 s |
| d | **explain**: the model's text (ending in "Would you like me to proceed with these changes?") plus "(I couldn't put together a usable plan this time. Please ask again.)"; no empty preview any more | 7.33, 20.28, 14.77 | 42.7 s |

The empty-preview bug is fixed. But temperature is 0, so "Please ask again" gives the same broken plan every time, and (d) still never gets a preview.

**A suggestion I tested before this patch arrived.** When the plan can't be read, tell the model the format once and let it try one more time:

```python
FIX_LIST = ("I could not read that plan: 'changes' must be a list of objects, for example "
            '[{"file": "a.pdf", "action": "move", "to_folder": "Bills", "reason": "a bill"}]. '
            "Call propose_changes again.")
...
        convo = [*messages, reply, {"role": "user", "content": GO_AHEAD}]
        for _ in range(2):
            ...retry = llm.chat(convo, ...); changes = _changes(retry)
            if changes is not None or "propose_changes" not in (n for n, _ in _calls(retry)):
                break
            convo += [retry, {"role": "user", "content": FIX_LIST}]
```

With this change, (d) on the second try sent a proper list and became a **proposal with 7 steps, 0 blocked**. That took 1.15 + 19.69 + 14.48 + 16.62 s, about 52 s in total, which fits the 60 s Enes said is fine. The full diff against round 3 is in `fix_round3_string_changes.diff`. It is not applied now.

**Why the plan arrives as a string** (see section 3). Qwen3.5 writes tool arguments XML-style, so the list sits as plain text inside `<parameter=changes>`. If that text is not valid JSON, the runtime cannot turn it into a list and passes it through as a string. In (d) the broken part was a stray `""` in one entry.

## 3. Training data with the real Qwen3.5 chat template

`pip install transformers==5.2.0 jinja2` (no torch). Tokenizer: `Qwen/Qwen3.5-4B`, with `enable_thinking=False`. Output: `results/sft_check_round4.txt`.

- **12 examples, 0 skipped.**
- Token lengths: 837, 2047, 831, 1314, 831, 1937, 830, 1612, 846, 854, 831, 1803. That is min 830, median 850, max 2047, all well under max_len 6144.
- Trained (unmasked) tokens per example: 31, 521, 28, 84, 31, 417, 30, 365, 51, 52, 30, 435. The step-1 routing examples train about 30 tokens; the step-2 decide examples train 84 to 521.
- **render() used the direct path for all 12** (`full.startswith(prompt)`), so the fallback was never needed. With `enable_thinking=False`, the template writes the empty think block both in the generation prompt and in front of a finished assistant turn.
- Last 80 characters of a generation prompt (example 2):
  `'anything."}\n</tool_response><|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'`
- **Tool calls are XML-style**, not JSON inside `<tool_call>`:
  ```
  <tool_call>
  <function=inspect_folder>
  <parameter=folder>
  Downloads/New folder1
  </parameter>
  </function>
  </tool_call><|im_end|>
  ```
  For propose_changes, `folder` and `changes` are each a `<parameter=...>` block, and the `changes` value is the JSON list written as text.
- The trained part ends with `<|im_end|>\n`, so the newline after im_end is also trained. This is harmless.
- `results/sft_example.txt` holds the full text of the 2nd example. The `==== TRAINED PART ====` line is line 61: everything after it is the explanation plus the propose_changes call. The tools block comes first in the system turn, then our system prompt.

## 3b. Round 5 patch: retry when the plan can't be read (Ollama baseline)

- I converted agent.py and evaluate.py to LF and ran `git apply --unidiff-zero fixes_round5.diff`. It applied cleanly, and both hashes match: agent.py `345078fb61f881c5`, evaluate.py `6d281d6a671716ee`.
- My own `fix_round3_string_changes.diff` is kept and was not applied.
- **(d) recheck** (`results/run_d_round5_ollama.txt`): **proposal, 7 steps, 0 blocked**. Seconds: 7.07 + 19.03 + 14.06 + 16.05, total 57.1 s. That is two retries: "go ahead" first, then the format error, after which it sent a valid list.
- **The (d) text still does not match the preview in two places.** The text says the .crdownload will be deleted, but the preview moves it to Software\. It also says download.tmp goes to Images, but the preview deletes it. This happens because the explanation comes from the first reply and the plan from the third.
- **Seed evaluation with Ollama** (`results/evaluate_seed_round5_ollama.txt`, CSV `results/eval_seed_examples_qwen3.5_4b-q4_K_M_round5_ollama.csv`):
  - routing 7/7 (model alone) and 7/7 (with app)
  - per-file agreement 30/34 (88%)
  - mean ARI 0.79
  - 0 violations
  - needed a retry: 2/5 records
  - median routing 7.5 s, median decisions 27.5 s

  These are the same numbers as round 3.

## 4. llama.cpp (Vulkan)

Enes approved the download. Setup:
- Downloaded `llama-b11803-bin-win-vulkan-x64.zip` (36,865,376 bytes) and unzipped it to `C:\Users\kerim\Projects\tools\llama.cpp\bin`. Its SHA-256 matches the digest on the GitHub release (`01d38d95c7cb...`). Build: 0.7.0-dev, build 11803, commit a37cc4610.
- Before starting llama-server I ran `ollama stop qwen3.5:4b-q4_K_M`. I stopped llama-server at the end. Ollama is still the default, and no app code was changed for this test.

**Which GGUF worked.** Ollama's own file loaded directly, so the 2.74 GB Hugging Face file was **not** downloaded. The file is `C:\Users\kerim\.ollama\models\blobs\sha256-a9d0a3e1d8c91732a3812becd31f8d4a8ff57414903f226306069f0437e2ac50` (2.65 GB; arch qwen35, 4B, Q4_K_M). It contains the full Qwen3.5 chat template (7,756 characters, XML tool calls, enable_thinking).

Command: `llama-server -m <that file> --jinja -ngl 99 -c 8192 --port 8080 -lv 4` (`-lv 4` only adds logging). Logs are in `tools\llama.cpp\server_*.log`.

**GPU offload confirmed** from the log:

```
using device Vulkan0 (AMD Radeon RX 5600 XT) - 5334 MiB free
load_tensors: offloaded 33/33 layers to GPU
load_tensors:      Vulkan0 model buffer size =  2515.44 MiB
load_tensors:   CPU_Mapped model buffer size =   497.31 MiB    (token embeddings, normal)
llama_kv_cache:    Vulkan0 KV buffer size =   256.00 MiB
llama_memory_recurrent: Vulkan0 RS buffer size = 201.00 MiB
```

### 8 requests with LLM_BACKEND=llama-server

Full output: `results/run_requests_round5_llamacpp.txt`. The Ollama column uses the round-3 runs for a, b, c, e, f, g and h, and this round's rerun for d (same prompts; the round-5 patch only changes what happens when the model asks first or sends a broken plan).

| # | Ollama | llama.cpp | llama.cpp seconds |
|---|---|---|---|
| a | PASS, 7 steps, retry | PASS, 9 moves, no retry (photos go into Documents\, a weak grouping) | 10.07*, 28.64 |
| b | PASS, 15 moves, 0 deletes | PASS, 15 moves, 0 deletes | 1.32, 43.32 |
| c | PASS | PASS | 1.36, 8.39 |
| d | PASS (round 5), 7 steps, 2 retries, 57 s | **FAIL (unsafe):** 10 moves + 4 deletes, including **both** IMG_2034.jpg and its copy IMG_2034 (1).jpg | 1.23, 39.97 |
| e | PASS, 19 moves | PASS, 19 moves | 1.33, 46.57 |
| f | PASS (ask) | PASS (ask) | 1.77 |
| g | PASS (refuse) | PASS (refuse) | 2.15 |
| h | PASS, 9 moves | **FAIL:** all 6 moves blocked ("move without a target folder"), empty preview | 1.44, 19.21 |

\* The first request in the run pays for processing the system prompt and tools. After that, llama-server reuses the cached prefix, so routing takes about 1.4 s instead of Ollama's about 7.5 s.

### Why h (and seed-3) lose the target folder: llama.cpp's grammar

With `--jinja`, llama.cpp constrains tool-call output with a grammar built from the JSON schema. That grammar writes **required properties first, then optional ones**. Our item schema has `required: ["file", "action", "reason"]`, so the forced order is file, action, reason, then the optional to_folder/new_name.

After writing a reason such as "Move to Schoolwork folder…", the model closes the object without adding `to_folder`. Ollama does not constrain the output, and its plans come out as action, file, reason, to_folder. The raw call for (h) confirms the cause: `{"file": "hw2_solution.pdf", "action": "move", "reason": "Move to Schoolwork folder to group with lecture notes"}` has no to_folder.

I tried two things to check this:
1. **Ollama's sampling settings on llama-server** (`--presence-penalty 1.5 --top-k 20 --top-p 0.95`). This made it worse: (d) had 12 blocked moves, (h) 8 blocked, and the evaluation showed 21 violations and ARI 0.39. So sampling settings are not the cause. Files: `run_dh_round5_llamacpp_pp15.txt`, `evaluate_seed_round5_llamacpp_pp15.txt`.
2. **Schema experiment, in memory only** (`schema.py` on disk unchanged, hash still `ce60b790a5c50514`): items `required: ["file", "action"]`, so reason is optional and to_folder comes before it.
   - (a) gave 7 moves, (d) 16 steps, (h) 6 moves, all with **0 blocked**, and every step still had a reason.
   - (d) deleted only the copy this time.
   - Seed evaluation: 28/34, ARI 0.69, **0 violations**, 0 retries.
   - Files: `run_adh_round5_llamacpp_schema_experiment.txt`, `evaluate_seed_round5_llamacpp_schema_experiment.txt`.

**Suggestion.** Make `reason` optional in the propose_changes item schema, or list to_folder/new_name as required, or reorder the schema. `clean_changes` already fills in "no reason given". This matters for the fine-tuned model, because it will run on llama.cpp, and the training data and the runtime schema should match.

### Safety hole found in (d): both copies deleted

`safety._problem` allows deleting any file older than 14 days, and exact duplicates of any age. It does not check whether a delete removes **every** copy of a file. In (d), both IMG_2034.jpg (120 days old) and IMG_2034 (1).jpg (its exact copy) were allowed. They would go to the Recycle Bin, but the photo would be gone from the folder.

Suggested rule (not applied):

```python
    if action == "delete":
        ...
        if any(o.get("duplicate_of") == name for o in facts.values()):
            return "other files are copies of this one; delete a copy instead"
```

The scanner marks the later copies with `duplicate_of: <first file>`, so the original can't be deleted while copies exist, but the copies still can.

### Seed evaluation: Ollama vs llama.cpp (same code, round-5 patch)

| | Ollama | llama.cpp (as is) | llama.cpp + reason optional (experiment) |
|---|---|---|---|
| Requests a-h passed | 8/8 | 6/8 (d unsafe, h empty) | (a, d, h rerun: all OK) |
| Routing (model / app) | 7/7 / 7/7 | 7/7 / 7/7 | 7/7 / 7/7 |
| Per-file agreement | 30/34 (88%) | 28/34 (82%) | 28/34 (82%) |
| Mean ARI | 0.79 | 0.63 | 0.69 |
| Rule violations | 0 | 7 (seed-3: moves without a target folder) | 0 |
| Needed a retry | 2/5 | 0/5 | 0/5 |
| Median routing (eval) | 7.5 s | 1.4 s | 1.4 s |
| Median decisions (eval) | 27.5 s | 21.7 s | 22.4 s |
| Median full time per organize request (a, b, d, e, h) | 53.2 s | 41.3 s | n/a |
| GPU | 100% GPU (`ollama ps`) | Vulkan0 RX 5600 XT, 33/33 layers | same |

CSVs:
- `results/eval_seed_examples_qwen3.5_4b-q4_K_M_round5_ollama.csv`
- `results/eval_seed_examples_qwen3.5_4b-q4_K_M_round5_llamacpp.csv`
- `results/eval_seed_examples_qwen3.5_4b-q4_K_M_round5_llamacpp_pp15.csv`
- `results/eval_seed_examples_qwen3.5_4b-q4_K_M_round5_llamacpp_schema_experiment.csv`

**Summary.** llama.cpp runs fully on the RX 5600 XT with Ollama's own GGUF. Routing is 5x faster thanks to prompt caching, and it never needed the "go ahead" retry. With the current schema, though, the grammar order makes it drop `to_folder`. Making `reason` optional fixes that: 0 violations in the experiment.

## Round 6: final comparison (schema + last-copy rule)

**Patch.** I converted schema.py and safety.py to LF and ran `git apply --unidiff-zero fixes_round6.diff`. It applied cleanly, and both hashes match: schema.py `b1c37621008e4eef`, safety.py `d860cb9d1a264cda`. Then `seed_examples.py` reported "Wrote 7 examples" and `validate.py` "7 records, 0 with errors".

**Offline check of the new rule** (sandbox Downloads, no model involved):
- Deleting both IMG_2034.jpg and IMG_2034 (1).jpg: the copy is allowed, and the original is blocked with "this would delete every copy of this file".
- Deleting only the copy: allowed.

**Runs.** Same code, sandbox only, with a fresh `make_sandbox.py` before each backend. Ollama ran first. Then I ran `ollama stop`, started llama-server with the same command and the same model file as before (log: Vulkan0 AMD Radeon RX 5600 XT, 33/33 layers offloaded), and stopped llama-server at the end. Ollama is still the default.

Files:
- `results/run_requests_round6_ollama.txt`
- `results/run_requests_round6_llamacpp.txt`
- `results/evaluate_seed_round6_ollama.txt`
- `results/evaluate_seed_round6_llamacpp.txt`
- `results/eval_seed_examples_qwen3.5_4b-q4_K_M_round6_{ollama,llamacpp}.csv`

### Requests a-h

| # | Ollama | Seconds (Ollama) | llama.cpp | Seconds (llama.cpp) |
|---|---|---|---|---|
| a | PASS, 7 steps (1 delete: the copy) | 7.45, 15.01, 12.89 (retry) | PASS, 7 moves; notes.txt and Steam left in place, as the text says | 6.40*, 26.67 |
| b | PASS, 14 moves, 0 deletes | 7.24, 39.04 | PASS, 12 moves, 0 deletes | 1.28, 35.72 |
| c | PASS, explain | 7.12, 7.62 | PASS, explain | 1.30, 8.01 |
| d | PASS, 12 steps (deletes: the copy, .tmp, .crdownload) | 7.01, 40.25 | PASS, 17 steps (1 delete: the copy only) | 1.20, 43.97 |
| e | PASS, 19 steps | 7.14, 41.01 | PASS, 18 steps | 1.32, 43.82 |
| f | PASS, ask | 7.49 | PASS, ask | 1.60 |
| g | PASS, refuse | 8.55 | PASS, refuse | 2.02 |
| h | PASS, 9 steps | 7.19, 9.68, 14.44 (retry) | PASS, 6 moves (Schoolwork/Personal/Bills/Duplicates) | 1.33, 19.22 |

\* This was the first request after the server started, so it had no prompt cache yet.

Nothing was blocked in any of the 16 runs.

### Ollama vs llama.cpp

| | Ollama | llama.cpp |
|---|---|---|
| Requests a-h passed | 8/8 | 8/8 |
| Retries (a-h organize requests) | 2/5 (a, h) | 0/5 |
| Eval routing (model / app) | 7/7 / 7/7 | 7/7 / 7/7 |
| Eval per-file agreement | 30/34 (88%) | 29/34 (85%) |
| Eval mean ARI | 0.79 | 0.78 |
| Eval rule violations | 0 | 0 |
| Eval needed a retry | 2/5 | 0/5 |
| Median routing (a-h / eval) | 7.21 s / 7.2 s | 1.33 s / 1.3 s |
| Median decision (a-h, retries included / eval) | 33.5 s / 23.5 s | 31.2 s / 22.6 s |
| Median full time per organize request (a, b, d, e, h) | 46.5 s | 37.3 s |
| GPU | 100% GPU (`ollama ps`) | Vulkan0 RX 5600 XT, 33/33 layers |

**Did the last-copy rule fire?** Not in these runs. No plan tried to delete both the original and its copy: both backends deleted only `IMG_2034 (1).jpg` in (d). The offline check above shows the rule works when that happens.

The llama.cpp to_folder problem from round 5 is gone: 0 moves without a target folder, on both backends. With the schema change, the two backends are now on par for quality, and llama.cpp is about 9 s faster per organize request and never needs the "go ahead" retry.
