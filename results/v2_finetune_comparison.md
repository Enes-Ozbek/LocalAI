# Fine-tuned 4B vs original 4B (llama.cpp, RX 5600 XT)

Date: 2026-10-22. Everything ran on `sandbox\` only, and no code on disk was changed. The two schema variants were applied in memory for the run only.

**Models.** Both are Qwen3.5-4B Q4_K_M on llama-server b11803 (Vulkan), started with `--jinja -ngl 99 -c 8192`:
- **Original:** Ollama's file `sha256-a9d0a3e1…` (33/33 layers on the GPU).
- **Fine-tuned:** `models\Qwen3.5-4B.Q4_K_M.gguf`, joined from 9 parts. SHA-256 `8eccfb24…` matches Colab's. It fully offloads (34/34 layers) and has its own Qwen3.5 template (7,991 characters).

**Data.**
- 8 sandbox requests a-h, the same as in earlier rounds.
- `dataset/test.jsonl`: 7 held-out records from 3 scenarios (4 organize, 2 refuse, 1 explain_only). Its SHA-256 is `13b61348…`.
- `dataset/seed_examples.jsonl`: **not held out**, because split.py always adds the seeds to the training data.

Raw outputs are in `results/finetune_compare/`:
- `<config>_requests.json`
- `<config>_eval_{test,seed_examples}.csv`
- `<config>_evaluate_*.txt`
- `summary.json`

## Why three schema variants

The fine-tuned model writes the keys of each change in the order **file, action, reason, to_folder**. It learned that order from the teacher drafts. Its unconstrained output, via `/completion` without a grammar, is a complete and sensible plan.

The current schema orders the keys file, action, to_folder, new_name, reason, with `required` set to file and action. llama.cpp's grammar allows optional keys only in that order. So once the model has written `reason`, it can no longer add `to_folder`, and the move gets blocked as "move without a target folder".

| Variant | Change (in memory only) |
|---|---|
| current | schema as on disk |
| reason-first | item properties ordered file, action, reason, to_folder, new_name |
| all-required | all 5 keys required, in schema order (the model must write every key) |

## Results

| | Original, current | Original, reason-first | Original, all-required | Fine-tuned, current | Fine-tuned, reason-first | Fine-tuned, all-required |
|---|---|---|---|---|---|---|
| Sandbox requests right (of 8) | **8** | 7 (h: 1 blocked) | **8** | 3 | **8** | **8** |
| Sandbox blocked changes | 0 | 1 | 0 | **55** | 0 | 0 |
| Steps per organize request (a, b, d, e, h) | 7, 12, 17, 18, 6 | 7, 13, 13, 19, 9 | 7, 15, 19, 18, 6 | 2, 0, 8, 3, 2 | 8, 19, 18, 18, 7 | 8, 19, 18, 18, 8 |
| test.jsonl routing | 5/7 | 5/7 | 5/7 | **7/7** | **7/7** | **7/7** |
| test.jsonl per-file agreement | 55/93 (59%) | 52/93 (56%) | 44/93 (47%) | 60/93 (65%) | 54/93 (58%) | **68/93 (73%)** |
| test.jsonl ARI | 0.34 | 0.35 | 0.26 | 0.27 | **0.40** | 0.34 |
| test.jsonl rule violations | 0 | 0 | 0 | 13 | 2 | 0 |
| seeds (not held out) routing | 7/7 | 7/7 | 7/7 | 6/7 | 6/7 | 6/7 |
| seeds per-file agreement | 29/34 (85%) | 30/34 (88%) | 29/34 (85%) | 29/34 (85%) | 29/34 (85%) | **31/34 (91%)** |
| seeds ARI | 0.78 | 0.78 | 0.75 | 0.81 | **0.88** | 0.85 |
| seeds rule violations | 0 | 0 | 0 | 7 | 2 | 1 |
| Retries (all runs) | 0 | 0 | 0 | 0 | 0 | 0 |
| Failed or empty plans | 0 | 0 | 0 | 0 | 0 | 0 |
| Deletes without a duplicate flag: installers / temp files (by design of the teacher prompt) | 0 | 2 | 2 | 13 | 12 | 13 |
| Deletes without a duplicate flag: **documents / archives / photos** | 0 | 1 | 1 (*) | **5** | **11** | **6** |
| "Duplicate" claimed for a file without a duplicate flag | 0 | 0 | 1 (*) | 2 | 2 | 3 |
| Inapplicable fields (to_folder on delete or rename, new_name on delete, new_name = own name) | 0 | 0 | **129** | 4 (†) | 5 (†) | **80** |
| Median routing (sandbox) | 1.46 s | 1.54 s | 1.65 s | 1.40 s | 1.50 s | 1.54 s |
| Median decision (sandbox) | 31.6 s | 33.3 s | 41.3 s | 28.5 s | 31.7 s | 42.6 s |
| Median full organize request (sandbox a, b, d, e, h) | **37.8 s** | 40.6 s | 53.1 s | 39.4 s | 44.4 s | 60.8 s |
| Median decision (test.jsonl) | 50.3 s | 53.6 s | 52.2 s | 41.5 s | 45.5 s | 52.3 s |

(*) The original model with all-required deleted IMG_2034.jpg as a "duplicate" and moved its exact copy IMG_2034 (1).jpg to Images. The photo still exists, so this is not data loss, but it deleted the original instead of the flagged copy.

(†) In the fine-tuned "current" and "reason-first" sandbox runs, the raw changes were not recorded (they started before I added that), so these two counts come from test.jsonl and the seeds only.

**Routing misses:**
- Original model: on both "clean up Program Files" test requests (s0009-1, s0014-0) it calls inspect_folder instead of refusing. In the app this folder name resolves to `C:\Users\…\Program Files`, which doesn't exist, so the user gets "I couldn't find the folder". It isn't harmful, but it isn't a refusal either.
- Fine-tuned model: refuses both. On seed-7 ("put my uni stuff in Documents/Uni into folders by course") it asks which folder instead of inspecting Documents/Uni.

## Risky deletes in detail

These are files of type document, archive or image, without a `duplicate_of` flag, that the model deleted. They passed the safety rules because they are older than 14 days.

- **Original, reason-first:** seed-7 `random.pdf`.
- **Fine-tuned, current:**
  - sandbox (d) `report_v1.docx`
  - test `report_final_REAL.pdf` ("Exact duplicate of report_final.pdf", which is false)
  - test `scan_0497.pdf` ("Exact duplicate of …", which is false)
  - test `Downloads_8.7z`
  - seed `random.pdf`
- **Fine-tuned, reason-first:**
  - test `project_report_draft.pdf`, `project_report_v1.pdf`, `project_report_v3.pdf`
  - test `report_final_REAL.pdf`, `essay_final_REAL.pdf`, `essay_v2.pdf`, `ideas.md`, `scan_0497.pdf`
  - test `Downloads_8.7z`
  - test `essay_v1.pdf` (blocked: changed in the last 14 days)
  - seed `random.pdf`
- **Fine-tuned, all-required:**
  - test `project_report_draft.pdf`, `project_report_final_REAL.pdf`, `project_report_v1.pdf`, `report_final_REAL.pdf`
  - test `photos_backup.zip` (272 days old)
  - seed `random.pdf`
- **Teacher labels in test.jsonl, for comparison:** 8 deletes of non-installer files without a duplicate flag: `IMG_2044.jpg` (photo), `Downloads_8.7z`, 3 project_report versions, `resume.docx`, `essay_final_REAL.pdf` and `Book1.xlsx`. 2 of them are justified with a false "duplicate" claim.

The fine-tuned model learned this behaviour from the teacher. The teacher prompt says to delete only exact duplicates, old installers and temp files, but the auto-accepted drafts also delete old document versions and archives. The safety layer only checks age, so it lets them through.

## Caveats

- **test.jsonl labels are unreviewed teacher drafts** (status needs_review, auto-accepted), and some of them are wrong (see above). Per-file agreement and ARI on test.jsonl therefore measure how closely a model imitates the teacher, not whether its plan is correct. Only 7 records from 3 scenarios is also very small.
- The seeds are in the training data, so the seed numbers are not a held-out result.
- The sandbox requests are the most meaningful check, but there are only 8.
- Each configuration ran once at temperature 0.

## Reading

1. **With the current schema, the fine-tuned model is unusable under llama.cpp:** 55 blocked moves, and (b) has an empty preview. The cause is the key order it learned, not its plans, which are complete when nothing constrains the output.
2. **reason-first fixes it at no cost in time.** It gives 8/8 requests, 0 blocked, and the best ARIs (test 0.40, seeds 0.88). But it also shows the most document deletes (11).
3. **all-required also gives 8/8 with 0 blocked,** plus the highest per-file agreement (73% test, 91% seeds) and 0 test violations. The downside is that it is about 30-40% slower (median 61 s vs 44 s per organize request). Both models then fill new_name with the file's own name (80 and 129 cases) and sometimes put to_folder on deletes. The app tolerates these values, but it is wasted output.
4. **The original model is the safest about deleting** (0 risky deletes with the current schema). The fine-tuned model groups better (ARI), refuses Program Files, and is about as fast. It has inherited the teacher's habit of deleting old document versions and archives, sometimes with false "duplicate" claims.

## Suggestions (not applied)

- **Schema:** use reason-first. It costs nothing in time and matches the fine-tuned model. Alternatively, normalize the key order in `sft_data.py` before the next training run, so that training data and schema agree.
- **Safety:** block a delete whose reason says "duplicate" when the file has no `duplicate_of` flag. And/or only allow deleting files without a flag when they are installers or temp/incomplete downloads.
- **Data:** review the drafts (`dataset/review.py`) before the next fine-tune, especially deletes of documents, archives and photos. The current model copies those mistakes.
