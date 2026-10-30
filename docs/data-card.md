# Data card: training and test data

All training and test data is simulated. No real person's files are used, so the data holds no personal
information. Every model and library we use allows this use (licences checked on 2026-10-24, below).

## What the data is

| Part | How it is made | Code |
| --- | --- | --- |
| Folders | Fake folders: about 5 to 30 files each, with invented names, sizes, dates and first lines of text, plus copies named the way Windows names them. Five kinds of user: student, office worker, phone photos, gamer, mixed. | `dataset/make_scenarios.py` |
| Requests | 4 requests per folder, from templates: organize, explain and organize, organize with a limit ("don't delete"), organize by a rule ("by year"), explain only, one named file (delete, rename, move), move to another folder, unclear folder, and refuse. About 15% contain typos. | `dataset/make_scenarios.py` |
| Answers | A bigger model (Qwen3.5-9B, the "teacher") writes the explanation and the plan. The answers to unclear-folder and refuse requests are fixed sentences. | `dataset/draft_answers.py` |
| Checks | Every answer goes through the app's own safety rules plus stricter delete rules: only duplicates, installers, temporary and empty files may be deleted, unless the user named the file. A change that breaks a rule is dropped. The answer is rejected when nothing is left, or when its text talks about a dropped file. | `dataset/validate.py` |
| Hand-written examples | 7 conversations written by the team, always in the training set | `dataset/seed_examples.jsonl` |
| Split | 80% train, 10% validation, 10% test, by folder. A test folder is never seen in training. | `dataset/split.py` |

The invented content uses common Turkish first names, real Turkish company names on invented bills (Turkcell,
Vodafone, IGDAS and others, with made-up amounts) and real course names. The sandbox for manual tests
(`make_sandbox.py`) uses the team's own names.

## How much data

`python dataset/stats.py` counts it. Each Kaggle run writes the table to `dataset/data_stats.md` inside
`training-data.zip`. A file decision is one file in a request that asks for changes. The answer moves, renames
or deletes it, or leaves it where it is.

| Round | Training conversations | Test conversations | File decisions | Note |
| --- | --- | --- | --- | --- |
| r7 | 66 | 7 | 714 files seen in training | Counted from the saved training file |
| r8 | about 200 | 25 | about 2,000 in training, 197 in testing | Estimate: the training file was not downloaded |
| r9 | to be filled from `data_stats.md` | | | Goal: over 5,000 file decisions |

## Ethics

- **No personal data:** every folder, file and request is generated. A public set of real documents (Govdocs1)
  was considered but not used.
- **Privacy by design:** the app runs fully on the user's PC. Files, requests and answers never leave it.
- **Safety:** the model can't change anything on its own. The user sees a preview and confirms first, deletes go
  to the Recycle Bin, every run can be undone, and system folders are refused.
- **Known bias:** requests are English only, and the folders follow five kinds of user, mostly Turkish
  university students. Other languages and kinds of users are untested.

## Licences

| Component | Used for | Licence |
| --- | --- | --- |
| [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/LICENSE) | The assistant's model, fine-tuned by us | Apache 2.0 |
| [Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B/blob/main/LICENSE) | The teacher that drafts training answers | Apache 2.0 |
| [Unsloth](https://github.com/unslothai/unsloth/blob/main/LICENSE) | Fine-tuning (the library only, not its AGPL-3.0 studio and CLI) | Apache 2.0 |
| [llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE) | Runs the model on the PC | MIT |
| [Ollama](https://github.com/ollama/ollama/blob/main/LICENSE) | Runs the teacher, and the app's fallback | MIT |
| pypdf, python-docx, Pillow, Send2Trash | Reading PDF, Word and photo files, the Recycle Bin | BSD-3-Clause, MIT, MIT-CMU, BSD-3-Clause |
| Our code, data and fine-tuned model | | The team's own work |

Apache 2.0 and MIT allow use, changes and sharing, including of a fine-tuned model, as long as the licence and
copyright notices are kept.
