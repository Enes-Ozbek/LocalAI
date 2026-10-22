# Local AI Assistant for Windows: folder organizer

Type a request like *"check files in my downloads/new folder1 and tell me what those are then organise them"*.
The assistant looks at the folder, explains what the files are, and proposes moves, renames and deletes,
with a reason for each one. Nothing changes until you confirm, deletes go to the Recycle Bin, and
`undo` reverses the last run. Everything runs on your PC with Qwen3.5-4B through Ollama.

## Setup (Windows)

```
ollama pull qwen3.5:4b-q4_K_M
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Try it on fake files first

```
python make_sandbox.py              # creates sandbox\Downloads, sandbox\Downloads\New folder1, sandbox\Desktop
python cli.py --base sandbox        # 'Downloads' now means sandbox\Downloads
```

Then type, for example:
- `check files in my downloads/new folder1 and tell me what those are then organise them`
- `organize downloads but don't delete anything`
- `what's on my desktop? don't change anything`
- `undo`

Without `--base`, folder names mean your real folders (C:\Users\you\Downloads and so on).

## How it works

```
your request -> model calls inspect_folder -> code scans the folder (facts per file)
             -> model explains + calls propose_changes -> safety rules -> preview -> you confirm -> run + undo log
```

| File | What it does |
| --- | --- |
| `assistant/schema.py` | System prompt and the 4 tools the model sees (shared with the training data) |
| `assistant/scanner.py` | Facts per file: type, size, dates, exact duplicates, first text of PDF/Word/text files, photo date |
| `assistant/safety.py` | Hard rules: protected folders, only folders the user named, no deleting recent files, respects "don't delete", no paths outside the folder |
| `assistant/agent.py` | The two-step conversation with the model. If the model only asks "shall I organize them?", it is told once to go ahead (the preview is the real confirmation) |
| `assistant/executor.py` | Preview, run (Recycle Bin for deletes), undo log in `~\.local-assistant\undo` |
| `cli.py` | Terminal chat |
| `make_sandbox.py` | Fake messy folders for testing |

## Building the training data

1. `python dataset/seed_examples.py`: 7 hand-written example conversations (copy their style).
2. `python dataset/make_scenarios.py --scenarios 300 --requests 4`: 1,200 tasks (fake folders + requests).
3. `python dataset/draft_answers.py --teacher <bigger model>`: a bigger model drafts each answer. Run it
   where a big GPU is available (for example Ollama on Colab). Drafts that break a rule are rejected automatically.
4. `python dataset/review.py`: you accept, edit or reject every draft. Only accepted ones are used.
5. `python dataset/split.py`: train / val / test, split by folder so the test folders are new to the model.

`python dataset/validate.py <file>` checks any data file against the same safety rules the app uses.

## Measuring

```
python bench/evaluate.py dataset/seed_examples.jsonl      # quick check
python bench/evaluate.py dataset/test.jsonl               # the real numbers for the report
python bench/evaluate.py dataset/test.jsonl --model qwen3.5:9b
```

Reports routing accuracy, per-file agreement with the correct answer, grouping agreement (Adjusted Rand
Index), rule violations and response time, and saves a CSV per record.

## Fine-tuning (Google Colab, free T4 GPU)

1. Unzip this project into Google Drive as `MyDrive/local-assistant`.
2. `colab/1_draft_answers.ipynb`: a bigger Qwen (the teacher) drafts an answer for each of the 1,200 tasks.
3. On your PC: `python dataset/review.py`, then `python dataset/split.py`. Copy train/val/test back to Drive.
4. `colab/2_finetune.ipynb`: trains a LoRA adapter on the 4B (16-bit; Unsloth advises against 4-bit QLoRA
   for Qwen3.5) and saves a `q4_k_m` GGUF file. Without train.jsonl it runs a short smoke test on the seeds.
   `train/sft_data.py` cuts every record into the two places the app asks the model and trains only the model's turn.
5. Run the GGUF on your PC with llama.cpp (Vulkan build for AMD cards):
   `llama-server -m models\assistant-4b.Q4_K_M.gguf --jinja -ngl 99 -c 8192 --port 8080`,
   then `set LLM_BACKEND=llama-server` and compare with `python bench/evaluate.py dataset/test.jsonl`.

## Next

- A desktop window instead of the terminal.
