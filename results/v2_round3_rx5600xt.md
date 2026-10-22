# v2 round 3 on RX 5600 XT: qwen3.5:4b-q4_K_M

Date: 2026-10-22. Same machine and model as rounds 1 and 2: RX 5600 XT, `ollama ps` shows 100% GPU and context 8192.
Folder: `C:\Users\kerim\Projects\local-assistant`. Everything ran on a fresh `sandbox\`. No real folders were touched, and there was no apply/undo this round.

## Patch

I saved the patch as `fixes_round3.diff`. Two problems came up when applying it, but neither changes the content:

1. **The last hunk (`dataset/seed_examples.py @@ -33,3 +33,3 @@`) had only 2 lines.** Its third context line, a blank line, was lost in transit. `git apply` said "corrupt patch at line 106". I changed that header to `@@ -33,2 +33,2 @@` in my copy of the diff.
2. **`dataset/seed_examples.py` has CRLF line endings in its original lines.** Even with `--ignore-whitespace`, that hunk would not match. I applied the other four files with `git apply --exclude=dataset/seed_examples.py` (all clean). I made the two `seed_examples.py` changes (the import line and `tool_result(facts)`) with an exact byte replace that keeps the file's line endings.

Then I ran `seed_examples.py` ("Wrote 7 examples") and `validate.py`, which reported "7 records, 0 with errors".

Hash check (CRLF replaced by LF, first 16 hex characters): **all 6 match.**

| File | Hash |
|---|---|
| assistant/schema.py | ce60b790a5c50514 |
| assistant/agent.py | a0d4c85e447448d8 |
| assistant/safety.py | 2555b41eef7878c9 |
| dataset/seed_examples.py | d21cdec0dca4a5bd |
| dataset/draft_answers.py | c509c2e7790b73c1 |
| dataset/seed_examples.jsonl | 25c5d3352537d252 |

## Requests a-h (`python test_run.py`)

Full output, with every reply text and preview: `results/run_requests_round3.txt`. `seconds` holds [routing, decide] or [routing, decide, retry].

| # | Expected | Got | Seconds | Retry | Total | Result |
|---|---|---|---|---|---|---|
| a | proposal | 6 moves (Academic/Personal/Bills/Installers) + 1 delete (duplicate). All reasons filled in | 7.78, 16.76, 15.21 | yes | 40.14 | PASS |
| b | proposal, no deletes | 15 moves, **0 deletes**; the text says the .tmp/.crdownload files are left alone | 7.63, 45.34 | **no** | 53.24 | PASS |
| c | explain only | explain, "I have not made any changes" | 7.60, 8.46 | n/a | 16.11 | PASS |
| d | proposal | kind "proposal" but **0 steps, 0 blocked**: the preview is empty although the text proposes moves | 7.46, 20.54, 14.80 | yes | 42.87 | **FAIL** |
| e | proposal | 19 moves (Spreadsheets/Documents/Installers/Images/Temp), no deletes | 7.58, 46.66 | **no** | 54.34 | PASS |
| f | ask which folder | "Which folder would you like me to organize?" | 8.01 | n/a | 8.01 | PASS |
| g | refuse | refuse | 8.71 | n/a | 8.71 | PASS |
| h | proposal | 9 moves (Documents/Images/Installers). 1 blocked: a delete of IMG_4411 (1).jpg, because that file was also moved ("file changed twice") | 7.64, 10.81, 19.07 | yes | 37.54 | PASS (text mismatch, see below) |

**7/8 pass.** The retry was needed in 3 of 5 organize requests (a, d, h), down from 5/5.

### Reply text checks

- **Empty reasons are fixed.** Every step in every preview has a reason, and the "no reason given" fallback was never needed.
- **The "please confirm" / "haven't specified which folder" leftovers are gone.** (a) now ends with "I will now organize these files…"; (b) and (e) describe what the preview shows.
- **(h) still contradicts its preview.** The text says "The duplicate image will be deleted", but the preview moves it to Images\. The delete was blocked because the model both moved and deleted that file.
- **(d) contradicts its (empty) preview.** The text says "I propose moving…" and lists installers to "Software". It also wrongly calls `download.tmp` a duplicate image.
- **(b) grouping is odd.** It puts the CVs into "Academic Documents". Not wrong, but a weak choice.

### Why (d) failed

I replayed (d) and logged the model calls; the result was the same. On the "go ahead" retry, the model called `propose_changes` with `changes` as a **JSON string instead of a list**, and the string is not even valid JSON. One entry ends in `"reason": "Delete temporary/incomplete download file", ""}`.

`clean_changes` then loops over the characters of the string, skips each one because it is not a dict, and returns `[]`. The app shows a "proposal" with no steps and no blocked items. There is no crash, but the user sees a plan described in text that is not in the preview.

I did **not** change the code. This is a suggested fix:

```diff
--- a/assistant/agent.py
+++ b/assistant/agent.py
@@
+import json
 import re
 import time
@@
 def _changes(message):
     """The proposed changes, or None when the model did not call propose_changes."""
-    return next((a.get("changes", []) for n, a in _calls(message) if n == "propose_changes"), None)
+    changes = next((a.get("changes", []) for n, a in _calls(message) if n == "propose_changes"), None)
+    if isinstance(changes, str):  # the 4B sometimes sends the list as a JSON string
+        try:
+            changes = json.loads(changes)
+        except ValueError:
+            return None  # unreadable plan: treat it like no plan
+    return changes if isinstance(changes, list) else None
```

With this fix, a string that parses becomes a normal plan. A broken string counts as "no plan", so the request ends as "explain" instead of an empty "proposal". It might also be worth one extra check in `run()`: if `allowed` and `blocked` are both empty, return "explain" with "I couldn't make a plan for this folder".

## evaluate.py on dataset/seed_examples.jsonl

Full output: `results/evaluate_seed_round3.txt`. CSV: `results/eval_seed_examples_qwen3.5_4b-q4_K_M_round3.csv`.

```
[1/7] seed-1 route OK (inspect_folder) files 7/9 ARI 0.526 violations 0
[2/7] seed-2 route OK (inspect_folder) files 5/5 ARI 1.0 violations 0
[3/7] seed-3 route OK (inspect_folder) files 8/9 ARI 0.442 violations 0
[4/7] seed-4 route OK (inspect_folder) files 5/5 ARI 1.0 violations 0
[5/7] seed-5 route OK (ask_clarification)
[6/7] seed-6 route OK (refuse)
[7/7] seed-7 route OK (inspect_folder) files 5/6 ARI 1.0 violations 0

== qwen3.5:4b-q4_K_M on 7 records (seed_examples.jsonl)
Routing (model alone): 7/7
Routing (with app):    7/7
Per-file agreement:    30/34 (88%)
Grouping (mean ARI):   0.79
Rule violations:       0 (in 0 records)
Needed 'go ahead':     2/5 records
Time: routing median 7.6s, decisions median 27.8s
```

- seed-3 ("don't delete anything") went from 1/9 to 8/9: it now sorts the folder instead of only renaming the duplicate.
- seed-7 now inspects Documents/Uni (in round 2 it inspected Downloads), so "with app" routing is 7/7.
- The retry was needed in seed-3 and seed-7.

## Round 1 / 2 / 3

The "full time per organize request" is the sum of step times for a, b, d, e and h. Round 1 is not directly comparable, because a and d stopped after asking and never made a plan.

| | Round 1 | Round 2 | Round 3 |
|---|---|---|---|
| Requests passed (a-h) | 5/8 | 8/8 | 7/8 (d: empty preview) |
| "Go ahead" retries (organize requests) | n/a | 5/5 | 3/5 |
| Median full time per organize request | 31.8 s | 54.2 s | 42.8 s |
| Median routing step (test_run) | 6.49 s | 7.52 s | 7.64 s |
| Eval routing, model alone | 6/7 | 7/7 | 7/7 |
| Eval routing, with app | n/a | 6/7 | 7/7 |
| Eval per-file agreement | 14/34 (41%) | 22/34 (65%) | **30/34 (88%)** |
| Eval mean ARI | 0.52 | 0.71 | **0.79** |
| Eval rule violations | 1 | 0 | 0 |
| Eval "go ahead" needed | n/a | 3/5 | 2/5 |
| Eval medians (routing / decisions) | 6.6 s / 16.3 s | 7.5 s / 25.6 s | 7.6 s / 27.8 s |
