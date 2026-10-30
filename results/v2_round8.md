# Round 8 verification: new key order and the "not a duplicate" rule

Date: 2026-10-22. Everything ran on `sandbox\` only. I tested the code exactly as it is on disk after this round's patch, with no in-memory changes.

## Patch check

- I converted the 6 target files to LF; README was already LF.
- `git apply --unidiff-zero` applied cleanly to all 6 files, and all hashes match:

| File | Hash |
|---|---|
| assistant/schema.py | 72fa6986552c5c71 |
| assistant/safety.py | 8c0b81603853add0 |
| dataset/validate.py | ad79ed11ee67bef8 |
| dataset/draft_answers.py | 8ebf018693c86052 |
| train/sft_data.py | 86ab805443868f0a |
| README.md | 01a0273a82038c63 |

- The notebook A4 script took the notebook from b8f018bf07100a59 to **f0a741a6a775f952** (matches).
- `validate.py dataset/seed_examples.jsonl`: 7 records, **0 with errors**.
- `validate.py dataset/test.jsonl`: 7 records, **3 with errors**, exactly s0009-2, s0014-3 and s0017-3. That is 8 errors in total:
  - IMG_2044.jpg, Downloads_8.7z and the 3 project_report versions: not a duplicate, installer or temp file.
  - resume.docx: "not a duplicate".
  - Book1.xlsx: not a duplicate, installer or temp file.
  - essay_final_REAL.pdf: "not a duplicate".
- Zip rebuilt as `C:\Users\kerim\Projects\local-assistant.zip` with 23 files (the previous 22 plus dataset/test.jsonl).

## Results

| | Fine-tuned, llama-server | Original 4B, Ollama (default setup) |
|---|---|---|
| Sandbox requests right | **8/8** | **8/8** |
| Sandbox blocked changes | 0 | 0 |
| Steps per organize request (a, b, d, e, h) | 8, 19, 18, 18, 7 | 7, 14, 12, 19, 9 |
| Retries (sandbox) | 0 | 2 ("go ahead" in a and h) |
| Median routing (sandbox) | 1.44 s | 8.21 s |
| Median decision (sandbox, retries included) | 31.4 s | 42.8 s |
| Median full organize request (a, b, d, e, h) | **44.4 s** | 56.0 s |
| test.jsonl routing | 7/7 | not run (only the requests, as asked) |
| test.jsonl per-file agreement / ARI | 54/93 (58%) / 0.40 | not run |
| test.jsonl rule violations | 4 (in 3 records) | not run |
| Seeds (not held out) routing | 6/7 (seed-7 asks instead of inspecting) | not run |
| Seeds per-file agreement / ARI | 29/34 (85%) / 0.88 | not run |
| Seeds rule violations | 2 (seed-3: deletes although "don't delete"; both blocked) | not run |
| Failed or empty plans | 0 | 0 |

The fine-tuned model matches the in-memory "reason-first" run from round 7 exactly (8/8, test 58% / 0.40, seeds 85% / 0.88), so the new key order on disk works as intended. Ollama has no grammar, so the new key order in the tool description doesn't hurt the default setup: 8/8 with nothing blocked.

### Blocked deletes in the fine-tuned runs (the new rule fired twice)

| Record | File | Rule |
|---|---|---|
| test s0014-3 | report_final_REAL.pdf ("exact duplicate of report_final.pdf", which is false) | **not a duplicate** (new) |
| test s0017-3 | scan_0497.pdf ("exact duplicate of electricity_bill…", which is false) | **not a duplicate** (new) |
| test s0017-3 | essay_v1.pdf | changed in the last 14 days |
| seeds seed-3 | IMG_4411 (1).jpg, SteamSetup.exe | you asked not to delete anything |

The 4th test violation is not a delete: in s0009-2, `CV_Enes_2025_updated.docx` was renamed with a different file extension.

### Deletes without a duplicate flag that still got through

| | Installers / temp files (allowed by design) | Documents / archives / photos |
|---|---|---|
| Fine-tuned (sandbox + test + seeds) | 13 | **8**: test s0009-2 Downloads_8.7z, project_report_draft.pdf, project_report_v1.pdf, project_report_v3.pdf; test s0017-3 essay_final_REAL.pdf, essay_v2.pdf, ideas.md; seed-7 random.pdf |
| Original on Ollama (sandbox) | 4 (download.tmp and Unconfirmed .crdownload in d and e) | **0** |

None of the deletes that got through says "duplicate" in its reason. They give reasons like "older draft superseded by …" or "no clear purpose", so the new rule doesn't apply to them. The remaining risk comes from what the model learned from the old teacher drafts. Only a retrain on data checked by the new validate.py will remove it, and with that validate.py, 3 of the 7 current test drafts would be dropped. The 14-day rule and the "don't delete" rule also still block what they should.

### Other notes

- **Filler values:**
  - The fine-tuned model put `to_folder='Deleted'` on 5 deletes in test s0009-2. This is harmless, because executor ignores to_folder for deletes.
  - Ollama filled `new_name` with the file's own name on 9 moves in (d). This is also harmless, because the target name is the same.
- **Ollama vs. round 6:** Ollama took 56 s per organize request this time, vs 46.5 s in round 6. Its routing is about 8 s, because Ollama re-reads the prompt on every request, while llama-server caches it (1.4 s).

Raw data is in `results/finetune_compare/`:
- `r8_finetuned_*` (requests JSON, evaluate txt/CSV)
- `r8_ollama_base_requests.json`
- `summary_r8.json`
- `_log_r8.txt`

llama-server is stopped. The Ollama model is still loaded (keep_alive 30 min), as in normal use.
