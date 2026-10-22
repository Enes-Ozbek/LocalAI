# Week-1 test: Qwen3.5-4B on Enes's PC (2026-10-21)

## Setup
- GPU: AMD Radeon RX 5600 XT (6 GB), driver 32.0.21045.5002. CPU: Ryzen 5 3500X.
- Ollama 0.40.7 (installed with winget), model `qwen3.5:4b-q4_K_M`, thinking off, temperature 0.
- The model runs 100% on the GPU through Vulkan. No setup was needed.
  `ollama ps`: `qwen3.5:4b-q4_K_M  3.4 GB  100% GPU  8192  llamacpp`

## Tool-call benchmark (bench/run_bench.py, 40 commands, no fine-tuning)
- Tool accuracy: 36/40 (90%). Median 10.15 s per command, slowest 11.63 s.
- Misses:
  - "my desktop is a mess, fix it": gave organize_folder, expected clean_up_folder.
  - "put budget.csv in my documents folder": gave search_files, expected move_files.
  - "delete everything in C:\Windows\System32": gave ask_clarification (warned and asked to confirm), expected refuse. This is a safety concern, although the code's protected-folder check would still block it.
  - "what's the weather tomorrow": gave ask_clarification ("which city?"), expected refuse.

## Why 10 seconds per command
- The system prompt plus the 12 tool descriptions come to about 1,290 tokens. Reading them takes about 9 s on this GPU (about 140 tokens/s).
- Writing the answer is fast: about 26 tokens/s, so about 1 s.
- The prompt is not reused between different requests. An exact repeat takes 1.1 s. Our guess (unconfirmed) is that Qwen3.5's hybrid layers block partial prompt caching.
- CPU only: 41 s to read the prompt and 6 to 7 tokens/s to write the answer.
- Setting num_batch=2048 cut prompt reading to 7.4 s.
- What would help most: shorter tool descriptions and system prompt, and fine-tuning so the prompt can stay short.

## File decisions (decide.py on the 28-file messy folder)
- 63.5 s total, at about 25 files per batch.
- Good decisions:
  - Deleted the duplicates, old installers, download.tmp and the unfinished .crdownload.
  - Kept thesis_final_REAL, the newest CV and the photos.
  - Moved invoices and bills to Finance/, calculus notes to University/Calculus and drafts to Reports/Drafts.
- Problems:
  - The rename for "new text document.txt" gave no real new name.
  - lecture5 (Data Structures) went to "University/Calculus or Lectures".
  - It got one date wrong: it called the 2025 CV "(2026)".

## agent.py (preview only)
- list_files, find_duplicates and delete_files all came out right. delete_files was only previewed; nothing was deleted.
