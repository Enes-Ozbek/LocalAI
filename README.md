# Local AI Assistant for Windows: folder organizer

Type a request like *"check files in my downloads/new folder1 and tell me what those are then organise them"*.
The assistant looks at the folder, explains what the files are, and proposes moves, renames and deletes,
with a reason for each one. Nothing changes until you confirm, deletes go to the Recycle Bin, and
`undo` reverses the last run, and takes deleted files back out of the Recycle Bin. Folders of any size work (big ones go to the model 40 files at a time).
Questions like *"how many files are there?"*, *"what's the biggest file in archive?"* or *"find my CV"* are
answered by the model with read-only tools: it decides what to look at, code counts, sorts and searches exactly.
It remembers the conversation, so *"and desktop?"* or *"organize it"* work as follow-ups. Everything runs on
your PC's GPU: our fine-tuned Qwen3.5-4B through llama.cpp,
or the stock Qwen3.5-4B through Ollama when the fine-tuned file isn't there.

## Setup (Windows)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

The model: put the fine-tuned `assistant-r8.Q4_K_M.gguf` in `models\` and llama.cpp's Windows Vulkan build in
`..\tools\llama.cpp\bin` (next to this folder). The app then starts llama.cpp by itself and stops it when you quit.
Without them it uses Ollama instead: `ollama pull qwen3.5:4b-q4_K_M` (or `set LLM_BACKEND=ollama` to choose it).

Search by meaning (optional): put `nomic-embed-text-v1.5.Q8_0.gguf` (about 140 MB, from
huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF) in `models\` too; the app starts it on port 8081 when you search.
Or `ollama pull nomic-embed-text` if you use Ollama. Without it, search works by name, folder and text.

## Try it on fake files first

```
python make_sandbox.py              # creates sandbox\Downloads, sandbox\Downloads\New folder1, sandbox\Desktop, sandbox\Archive (160 files), empty sandbox\Documents and sandbox\Pictures
python cli.py --base sandbox        # 'Downloads' now means sandbox\Downloads
```

Then type, for example:
- `check files in my downloads/new folder1 and tell me what those are then organise them`
- `organize downloads but don't delete anything`
- `what's on my desktop? don't change anything`
- `delete all the .tmp files in archive` (a big folder: only those files go to the model)
- `organize archive` (160 files: 4 parts, about a minute each)
- `how many files are there?`, then `how many are in downloads?`, then `and desktop?`
- `what's the biggest file in archive?`, `how many pdfs are in archive?`, `how big is my desktop?`
- `find my cv`, `where are the pdfs from last month`, `find duplicates in archive`
- `undo`

Without `--base`, folder names mean your real folders (C:\Users\you\Downloads and so on).

## The app window

```
python app.py --base sandbox        # opens http://127.0.0.1:8765 in your browser
```

The same assistant with a window: pick an example or type a request in the box at the bottom. A plan is shown like
a folder window: on the left the folder now, one row per file with a checkbox and where it goes; on the right the
folder after the plan, in big folder and file icons (new folders marked "New", renamed files "Renamed", deletes on
one "Going to the Recycle Bin" line). Double-click a folder to look inside, click anything to see why. The right side
is worked out from your ticks, so it changes the moment you untick a file. Nothing is written until you press Apply;
then the page shows the folder as it is now, with Undo. "Undo last run" (top right) reverses the last applied
changes, deleted files included (they come back out of the Recycle Bin, as long as it hasn't been emptied).
A request runs in the app, not in the page: the page shows a progress bar and the time, and if you reload it or
open a second window, it shows the request that is still running instead of starting another. Questions and search results get their own screens. The page is served on this PC only, only the page the
app opened can use it, and nothing is loaded from the internet (the Figtree font is in `assistant/fonts`, SIL Open
Font License). Light and dark follow Windows until you press the Light/Dark button at the top right, which is remembered. Press Ctrl+C in the terminal to quit.
Options as for `cli.py` (`--base`, `--model`), plus `--port` and `--no-browser`.

Or double-click `Folder Assistant.bat`: it asks whether to use the test sandbox (Enter) or your real folders
(n), then opens the window. For a desktop icon: right-click it > Send to > Desktop (create shortcut).

## How it works

```
your request -> model calls inspect_folder -> code scans the folder (facts per file)
             -> model explains + calls propose_changes -> safety rules -> preview -> you confirm -> run + undo log
a question   -> model calls list_files / folder_summary / search_files (up to 4 times) -> code counts, sorts, searches
             -> model answers from the results -> files shown -> tick some -> delete or move them -> preview -> you confirm
```

**Questions.** "How many", "how big", "the biggest/newest/oldest", "find", "where is", "is there", "do I have"
and "list" go to the model with a second prompt and three read-only tools (`schema.READ_TOOLS`, run by `answers.run_tool`) instead of the
organizing prompt it was trained on: `list_files` (filter by kind, extension, name, age, size; sort by size, date
or name; count; limit), `folder_summary` (kinds, sizes, subfolders; without a folder, each main folder) and
`search_files`. The model never counts itself. If it answers without looking, or doesn't finish in 4 tool calls,
code answers instead (`answers.answer`, `search.find`). `QUESTION_MODE=code` skips the model for questions.
Requests that change files still go the trained way.

**Follow-ups.** `agent.Session` keeps the conversation: an answer to "Which folder do you mean?" finishes the
request that asked it ("organize my files" -> "downloads"), "and desktop?" repeats the last question for another
folder, "organize it" / "delete the tmp files there" mean the folder talked about last, and with a question the
model sees the last 3 requests and answers.

**Big folders.** The model sees at most 40 files per request (more makes the prompt too long for a 4B model). A
bigger folder goes in parts of 40, each looking to the model like a normal request; the folders earlier parts
create are listed as subfolders in the later parts, so they reuse the same names. All the changes then go through
the safety rules together. When the request names files, only those (and their copies) go; when it names a kind
of file ("all .tmp files", "the photos", "pdfs older than a year", "screenshots from 2024", "videos bigger than
1 GB", "files with IMG in the name"), only the files of that kind go (`filters.py`), so bulk jobs stay quick.
At most 10 parts (400 files) per request.

| File | What it does |
| --- | --- |
| `assistant/schema.py` | System prompt and the 4 tools the model sees (shared with the training data) |
| `assistant/scanner.py` | Facts per file: type, size, dates, exact duplicates, first text of PDF/Word/text files, photo date |
| `assistant/safety.py` | Hard rules: protected folders, only folders the user named, no deleting files changed in the last 14 days unless you named them or asked to delete that kind ("delete all .tmp files"), respects "don't delete", no paths outside the folder unless the request names that folder (see agent.py), never deletes every copy of a file, no deleting a file called a "duplicate" when the scan found no copy, or called "temporary" or "broken" when it is a normal file, refuses system folders even when named without C:\. A request that names files, or picks one by position ("delete the last file" = the newest, also oldest, biggest, smallest), changes only those files |
| `assistant/agent.py` | The two-step conversation with the model. If the model only asks "shall I organize them?", it is told once to go ahead (the preview is the real confirmation). If it plans before reading the folder, or changes another file than the one you named, it is told once to fix that. "Move CV.docx from downloads to documents" goes to your Documents folder (not a new Downloads\Documents) when the request names it, it already exists and the request isn't about organizing the whole folder; the preview then shows the full path |
| `assistant/filters.py` | Reads which files a request is about: kinds, extensions, names, dates, sizes. Folder names ("move the pdfs to documents") are not kinds of file |
| `assistant/answers.py` | The read-only tools for questions (list, summary, search) and the code answers used when the model doesn't look |
| `assistant/search.py` | Search without the chat model: your user folders (wherever Windows keeps them, also in OneDrive) and their subfolders, or the folder the request names ("in downloads"). Scores the name, the folders and the first text of documents; with an embedding model also the meaning ("resume" finds a CV). Document text and meanings are kept in `~\.local-assistant\search.sqlite` |
| `assistant/executor.py` | Preview, run (Recycle Bin for deletes), undo log in `~\.local-assistant\undo`; undo moves files back and restores deleted ones from the Recycle Bin (it reads the bin's `$I` entries; on Linux the trash's `.trashinfo` files) |
| `cli.py` | Terminal chat |
| `app.py`, `assistant/app.html`, `assistant/fonts/`, `Folder Assistant.bat` | The app window: a page on 127.0.0.1 for the same request, a before/after folder view, apply and undo; the .bat opens it with a double-click |
| `make_sandbox.py` | Fake messy folders for testing |

## Building the training data

1. `python dataset/seed_examples.py`: 7 hand-written example conversations (copy their style).
2. `python dataset/make_scenarios.py --scenarios 300 --requests 4`: 1,200 tasks (fake folders + requests, including
   requests about one named file and moves to another folder). More folders keep the first ones the same.
3. `python dataset/draft_answers.py --teacher <bigger model>`: a bigger model drafts each answer. Run it
   where a big GPU is available (for example Ollama on Kaggle; `--shard 0/2` and `--shard 1/2` split the work over
   two GPUs). A change that breaks a rule is dropped; the draft is rejected when nothing is left or its text talks
   about the dropped file.
4. `python dataset/review.py`: you accept, edit or reject every draft. Only accepted ones are used.
5. `python dataset/make_questions.py`: 2 question conversations per folder (600 for 300 folders): "how many files
   are there?", "the biggest file in archive", "find my cv", follow-ups like "and desktop?". The model calls the
   read-only tools and answers from what they return; the tool results come from the same code the app uses, so
   no teacher model is needed and every number is right.
6. `python dataset/split.py`: train / val / test, split by folder so the test folders are new to the model.
7. `python dataset/stats.py`: conversations, training examples and file decisions per split, for the report.

Where the data comes from, its licences and the ethics check: [docs/data-card.md](docs/data-card.md).

`python dataset/validate.py <file>` checks any data file against the same safety rules the app uses.
It is stricter in one way: a training answer may delete only exact duplicates, installers, temporary or
broken downloads and empty files, unless the user named the file. The first fine-tuned model copied the teacher's
deletes of old document versions and photos, so changes like that are dropped.

## Tests

```
pip install pytest
python -m pytest
```

49 tests of the safety rules, the request flow (with scripted model answers), big folders in parts, request
filters, search, questions and follow-ups, Apply + Undo on a sandbox, and the data pipeline. No model or GPU is needed. The test folders go in `.pytest_tmp` in the project, because Windows keeps temporary folders in AppData, which the safety rules protect. GitHub runs them on Windows and Linux on every push
(`.github/workflows/tests.yml`).

## Measuring

```
python bench/evaluate.py dataset/seed_examples.jsonl      # quick check
python bench/evaluate.py dataset/test.jsonl               # the real numbers for the report
python bench/evaluate.py dataset/test.jsonl --model qwen3.5:9b
```

Reports routing accuracy, per-file agreement with the correct answer, grouping agreement (Adjusted Rand
Index), rule violations and response time, and saves a CSV per record.

```
python make_sandbox.py
python bench/questions.py                                 # 17 questions about the sandbox, with follow-ups
```

Checks each answer against exact counts and says how many the model answered itself (with its tools) and how many
needed the code backup, with the tool calls and times, in `results\questions_<model>.csv`.

## Fine-tuning (Google Colab, free T4 GPU)

Open `colab/finetune_colab.ipynb` in Colab (File > Upload notebook), choose a T4 GPU and run the cells in order.
It asks you to upload this zip. Every 150 drafted answers it downloads `local-assistant-progress.zip`;
after a disconnect, upload that one instead and it continues.
Already ran Part A once? The Shortcut cell takes the answers from that run's progress zip, A4 checks them
again with today's rules, and only Part B has to run.
Colab's free GPU used up? `colab/finetune_kaggle.ipynb` does the same on Kaggle (about 30 free GPU hours a week,
choose GPU T4 x2 and turn Internet on). The code is inside that notebook, so you only add your progress zip as
Input; the download links appear under B7. After changing any code, run `python colab/embed_code.py` to put the
new code into it.
- Part A (about 4 to 5 hours on Kaggle's two GPUs): a bigger Qwen (the teacher) drafts answers for 1,200 fake folder
  requests; changes that break a safety rule are dropped. A4 counts the data and gives you `training-data.zip`:
  keep it for the report. For better data, review the drafts on your PC with `python dataset/review.py`.
- Part B (about 2 hours): trains a LoRA adapter on the 4B (16-bit as Unsloth advises for Qwen3.5 on GPUs with
  20 GB or more; 4-bit on the free T4, which has too little memory for 16-bit) and downloads a `q4_k_m` GGUF file
  in 300 MB parts; join them with `python train/join_parts.py models`. Without data it runs a 5-step test on the
  seed examples.
  `train/sft_data.py` cuts every record into the two places the app asks the model and trains only the model's turn.
- Run the GGUF on your PC: `python cli.py` or `python bench/evaluate.py dataset/test.jsonl` start llama.cpp with
  `models\assistant-r8.Q4_K_M.gguf` (`set LLM_GGUF=<file>` for another one). `set LLM_BACKEND=ollama` compares
  with the stock model.
- Results so far (`results\v3_retrain_comparison.md` on Enes's PC): the retrained model (r8) proposed 1 delete of
  a non-duplicate document across all tests, against 9 for the first fine-tune (r7) and 6 for the original 4B.
