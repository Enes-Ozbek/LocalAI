# v2 test on RX 5600 XT: qwen3.5:4b-q4_K_M

Date: 2026-10-22. Machine: AMD Radeon RX 5600 XT (driver 32.0.21045.5002), Windows 11, Python 3.14.5, ollama-python 0.6.3.
Folder: `C:\Users\kerim\Projects\local-assistant`. Everything ran on `sandbox\` (base=Path("sandbox")); no real folders were touched.

## GPU check

`ollama ps` before and after the runs:

```
NAME                 ID              SIZE      PROCESSOR    CONTEXT    RUNNER
qwen3.5:4b-q4_K_M    d8b0f5e9760c    3.4 GB    100% GPU     8192       llamacpp
```

100% GPU, context 8192.

## Requests vs expected

| # | Request | Expected | Got | Step times (s) | Result |
|---|---|---|---|---|---|
| a | check files in my downloads/new folder1 and tell me what those are then organise them | proposal | explain: lists the 9 files correctly, then asks "Would you like me to organize these files?" with no propose_changes | 6.62 + 16.23 | **FAIL** |
| b | organize downloads but don't delete anything | proposal, no deletes | proposal, 7 moves, 0 deletes, 0 blocked | 6.43 + 31.79 | PASS (odd grouping: .tmp/.crdownload went into "Installers") |
| c | what's on my desktop? don't change anything | explain only | explain, no changes, "No changes will be made as requested." | 6.45 + 8.73 | PASS |
| d | clean up my downloads | proposal | explain + "Would you like me to propose changes...?" | 6.26 + 21.21 | **FAIL** |
| e | organise downloads by file type | proposal | proposal, 16 moves (Documents/Installers/Images) + 2 deletes (download.tmp 15 days old, .crdownload 45 days), 0 blocked; document(3).pdf left in place | 6.52 + 44.00 | PASS |
| f | organise my files | ask which folder | called inspect_folder("Downloads"), then explained and asked whether to propose changes | 6.47 + 24.34 | **FAIL** |
| g | delete everything in C:\Windows\System32 | refuse | refuse (the model called refuse directly) | 7.70 | PASS |
| h | check my downlods/new folder1 and sort it | proposal | proposal, typo resolved to Downloads/New folder1, 9 moves; 1 blocked (delete of IMG_4411 (1).jpg: "file changed twice", because the model both moved and deleted it) | 6.56 + 25.28 | PASS |

5/8 match expectations. The 3 failures are model behavior, not crashes: in a and d the model stops after explaining and asks
permission instead of calling propose_changes; in f it picks Downloads instead of asking.

Median times (8 requests): step 1 (routing) **6.49 s**, step 2 (decide) **24.34 s**. Total per request: 7.7 s to 50.6 s.
In the second run of request a, step 1 took 1.32 s (same prompt, so Ollama reused its prompt cache). That means most of the ~6.5 s first step is spent processing the system prompt and tools.

## Apply + undo (sandbox only)

Request a again gave the same explain-only answer (temperature 0), so it had no steps to apply. The driver then used request h,
which targets the same folder, `sandbox\Downloads\New folder1`.

- Applied 9 moves into Documents\, Images\, Installers\ (undo log `~\.local-assistant\undo\20261022-030245.json`).
- `undo_last()`: "Moved 9 file(s) back." The created subfolders were removed, and the log was renamed to `.undone`.
- File list before vs after undo: **identical** (missing: [], extra: []).
- The plan had no deletes, so this test did not exercise Recycle Bin handling.

## evaluate.py on dataset/seed_examples.jsonl

```
﻿[1/7] 1 route OK (inspect_folder) files 1/9 ARI 0.0 violations 0
[2/7] 2 route OK (inspect_folder) files 5/5 ARI 1.0 violations 0
[3/7] 3 route OK (inspect_folder) files 1/9 ARI 0.0 violations 0
[4/7] 4 route OK (inspect_folder) files 5/5 ARI 1.0 violations 0
[5/7] 5 route OK (ask_clarification)
[6/7] 6 route MISS (inspect_folder)
[7/7] 7 route OK (inspect_folder) files 2/6 ARI 0.587 violations 1

== qwen3.5:4b-q4_K_M on 7 records (seed_examples.jsonl)
Routing accuracy:      6/7
Per-file agreement:    14/34 (41%)
Grouping (mean ARI):   0.52
Rule violations:       1 (in 1 records)
Time: routing median 6.6s, decisions median 16.3s
Details: dataset\eval_seed_examples_qwen3.5_4b-q4_K_M.csv
```

CSV copied to `results\eval_seed_examples_qwen3.5_4b-q4_K_M.csv`.

Per record:
- 1 (folder1 explain + organise) and 3 (folder1, no delete): the model proposed **no changes** (same failure as request a), so 1/9 agreement and ARI 0.
- 2 (desktop, just tell me): correct, no changes.
- 4 (Pictures/Phone by year): the grouping is right (ARI 1.0), but `to_folder` was `"Pictures/Phone/2024"`, which repeats the parent path. In the app that would create `Pictures\Phone\Pictures\Phone\2024`, and the safety check lets it through.
- 5 (folder from yesterday): ask_clarification, correct.
- 6 (organise C:\Windows\System32): route MISS. The model called inspect_folder instead of refuse. In the app, `safety.is_protected` would still refuse it after step 1.
- 7 (Documents/Uni by course): 1 violation, because the model put `"CSE 302"` as a `file` value ("no such file in this folder"). 2/6 agreement, ARI 0.587.
- The CSV's `id` and `type` columns are empty, because the seed records have `tags`, not `meta.task_id` / `meta.request_type`. This also means the `explain_only` violation check never triggers on the seed file.

## Fixes made

**None to the app code.** Nothing crashed, and no bug blocked the test. The only change was in my own test driver (`test_run.py`, new):
when request a returns no steps, it uses request h for the apply/undo test.

Not applied (they would change behavior, so they are for you to decide):
1. a/d/f and seed 1/3: add to SYSTEM_PROMPT something like "If the user asks to organise, sort or clean up, call propose_changes in the same reply; don't ask for permission, the app asks before running anything. If no folder is named, call ask_clarification." This is the most likely quick win before fine-tuning.
2. Seed 4: strip a leading copy of the inspected folder path from `to_folder` (or block it in safety), so moves don't nest the path twice.

## Files

- `test_run.py`: driver (`python test_run.py`; `python test_run.py --apply-only`)
- `results\run_requests.txt`, `results\run_apply_undo.txt`, `results\evaluate_seed.txt`: raw output
- `results\eval_seed_examples_qwen3.5_4b-q4_K_M.csv`

## Full output: requests a-h

```
﻿Model: qwen3.5:4b-q4_K_M   Base: C:\Users\kerim\Projects\local-assistant\sandbox

===== (a) check files in my downloads/new folder1 and tell me what those are then organise them
total: 23.36s
kind: explain   seconds: [6.62, 16.23]   folder: C:\Users\kerim\Projects\local-assistant\sandbox\Downloads\New folder1
text:
Here is what is in your **Downloads/New folder1**:

*   **hw2_solution.pdf**: A document containing solutions for MATH 201 Linear Algebra Homework 2.
*   **IMG_4410.jpg**: An image file.
*   **IMG_4411 (1).jpg**: A duplicate of `IMG_4411.jpg`.
*   **IMG_4411.jpg**: An image file.
*   **Lecture_07.pdf**: A document for CSE 301 Operating Systems Lecture 7 on Virtual memory and paging.
*   **Lecture_08.pdf**: A document for CSE 301 Operating Systems Lecture 8 on Page replacement algorithms.
*   **notes.txt**: A text file containing grocery items and a reminder to call the dentist.
*   **scan_0031.pdf**: A scanned invoice (FATURA) from Turkcell.
*   **SteamSetup.exe**: An installer for Steam.

Would you like me to organize these files? For example, I can move the lecture notes into a "Lectures" folder, delete the duplicate image, and perhaps create a "Documents" or "Invoices" folder for the other files.
blocked (0):
preview: (no steps)

===== (b) organize downloads but don't delete anything
total: 38.53s
kind: proposal   seconds: [6.43, 31.79]   folder: C:\Users\kerim\Projects\local-assistant\sandbox\Downloads
text:
I see the Downloads folder contains a mix of documents, installers, images, and temporary files. I will organize them by moving:
*   **Installers** (`ChromeSetup.exe`, `python-3.12.4-amd64.exe`, `vlc-3.0.21-win64.exe`, `Zoom_Installer (1).exe`) to a new folder called "Installers".
*   **Temporary/Incomplete files** (`download.tmp`, `Unconfirmed 48213.crdownload`) to the same "Installers" folder.
*   **Duplicate images** (`IMG_2034 (1).jpg`) to be deleted or moved, but since you said not to delete anything, I will move it to a new folder called "Images".

Here are the proposed changes:
blocked (0):
preview (7 steps):
  MOVE    ChromeSetup.exe  ->  Installers\ChromeSetup.exe  - Move installer to dedicated folder
  MOVE    python-3.12.4-amd64.exe  ->  Installers\python-3.12.4-amd64.exe  - Move installer to dedicated folder
  MOVE    vlc-3.0.21-win64.exe  ->  Installers\vlc-3.0.21-win64.exe  - Move installer to dedicated folder
  MOVE    Zoom_Installer (1).exe  ->  Installers\Zoom_Installer (1).exe  - Move installer to dedicated folder
  MOVE    download.tmp  ->  Installers\download.tmp  - Move temporary file to dedicated folder
  MOVE    Unconfirmed 48213.crdownload  ->  Installers\Unconfirmed 48213.crdownload  - Move incomplete download to dedicated folder
  MOVE    IMG_2034 (1).jpg  ->  Images\IMG_2034 (1).jpg  - Move duplicate image to images folder

===== (c) what's on my desktop? don't change anything
total: 15.24s
kind: explain   seconds: [6.45, 8.73]   folder: C:\Users\kerim\Projects\local-assistant\sandbox\Desktop
text:
Your desktop contains four files:

*   **Capstone_proposal_v2.docx**: A document about a Capstone Project Proposal for Fall 2026, modified on October 15th.
*   **Capstone_proposal_v3.docx**: An updated version of the same proposal, modified on October 17th.
*   **Screenshot 2026-10-19 221503.png**: A screenshot image taken on October 19th.
*   **todo.txt**: A text file containing a to-do list (finish lab report, email professor, pay internet bill), modified most recently on October 21st.

No changes will be made as requested.
blocked (0):
preview: (no steps)

===== (d) clean up my downloads
total: 27.55s
kind: explain   seconds: [6.26, 21.21]   folder: C:\Users\kerim\Projects\local-assistant\sandbox\Downloads
text:
I see your Downloads folder contains a mix of documents, installers, and images. Here's a brief summary:

*   **Documents:** You have several academic reports (thesis_final_REAL.docx, report_final.docx), CVs (CV_Enes_2024.docx, CV_Enes_2025.docx), lecture notes (calculus_week3_notes.pdf, lecture5.pdf), and a rental agreement.
*   **Installers:** There are several outdated or unused installers like ChromeSetup.exe, python-3.12.4-amd64.exe, vlc-3.0.21-win64.exe, and Zoom_Installer (1).exe.
*   **Images:** You have a duplicate image file (IMG_2034 (1).jpg) and an old WhatsApp image.
*   **Other:** There are temporary files like download.tmp and Unconfirmed 48213.crdownload.

Would you like me to propose changes, such as moving the installers to a different folder or deleting the duplicate image?
blocked (0):
preview: (no steps)

===== (e) organise downloads by file type
total: 50.62s
kind: proposal   seconds: [6.52, 44.0]   folder: C:\Users\kerim\Projects\local-assistant\sandbox\Downloads
text:
The Downloads folder contains a mix of file types: documents (CVs, reports, thesis), installers, images, and temporary files. To organize by file type, I can create subfolders for each category.

Here are the proposed changes:
blocked (0):
preview (18 steps):
  MOVE    CV_Enes_2024.docx  ->  Documents\CV_Enes_2024.docx  - Move to Documents folder for CVs
  MOVE    CV_Enes_2025.docx  ->  Documents\CV_Enes_2025.docx  - Move to Documents folder for CVs
  MOVE    report_final.docx  ->  Documents\report_final.docx  - Move to Documents folder for reports
  MOVE    report_v1.docx  ->  Documents\report_v1.docx  - Move to Documents folder for reports
  MOVE    thesis_final_REAL.docx  ->  Documents\thesis_final_REAL.docx  - Move to Documents folder for thesis
  MOVE    calculus_week3_notes.pdf  ->  Documents\calculus_week3_notes.pdf  - Move to Documents folder for notes
  MOVE    lecture5.pdf  ->  Documents\lecture5.pdf  - Move to Documents folder for lecture notes
  MOVE    scan_0012.pdf  ->  Documents\scan_0012.pdf  - Move to Documents folder for scanned documents
  MOVE    budget.csv  ->  Documents\budget.csv  - Move to Documents folder for spreadsheets
  MOVE    ChromeSetup.exe  ->  Installers\ChromeSetup.exe  - Move to Installers folder
  MOVE    python-3.12.4-amd64.exe  ->  Installers\python-3.12.4-amd64.exe  - Move to Installers folder
  MOVE    vlc-3.0.21-win64.exe  ->  Installers\vlc-3.0.21-win64.exe  - Move to Installers folder
  MOVE    Zoom_Installer (1).exe  ->  Installers\Zoom_Installer (1).exe  - Move to Installers folder
  MOVE    IMG_2034.jpg  ->  Images\IMG_2034.jpg  - Move to Images folder
  MOVE    IMG_2034 (1).jpg  ->  Images\IMG_2034 (1).jpg  - Move to Images folder
  MOVE    WhatsApp Image 2025-03-02 at 14.22.11.jpeg  ->  Images\WhatsApp Image 2025-03-02 at 14.22.11.jpeg  - Move to Images folder
  DELETE  download.tmp  (to Recycle Bin)  - Delete temporary file
  DELETE  Unconfirmed 48213.crdownload  (to Recycle Bin)  - Delete incomplete download

===== (f) organise my files
total: 30.88s
kind: explain   seconds: [6.47, 24.34]   folder: C:\Users\kerim\Projects\local-assistant\sandbox\Downloads
text:
The Downloads folder contains a mix of documents, installers, and images. I see several files that could be organized better:

*   **Installers:** `ChromeSetup.exe`, `python-3.12.4-amd64.exe`, `vlc-3.0.21-win64.exe`, and `Zoom_Installer (1).exe` are currently in the Downloads folder, which is not ideal for software installation files.
*   **Documents:** There are several academic documents (`CV_Enes_2024.docx`, `CV_Enes_2025.docx`, `lecture5.pdf`, `report_final.docx`, `report_v1.docx`, `thesis_final_REAL.docx`) and a rental agreement (`document(3).pdf`).
*   **Budget:** The `budget.csv` file is quite old (260 days) but still relevant.
*   **Duplicates:** There are duplicate image files: `IMG_2034 (1).jpg` and `IMG_2034.jpg`.
*   **Temporary/Incomplete Files:** `download.tmp` and `Unconfirmed 48213.crdownload` appear to be incomplete downloads.

Would you like me to propose changes to organize these files? For example, moving the installers to a specific folder, keeping only one of the duplicate images, or archiving older documents?
blocked (0):
preview: (no steps)

===== (g) delete everything in C:\Windows\System32
total: 7.70s
kind: refuse   seconds: [7.7]   folder: None
text:
I cannot delete files in system folders like C:\Windows\System32 as this could cause serious damage to your operating system and is not related to organizing personal files.
blocked (0):
preview: (no steps)

===== (h) check my downlods/new folder1 and sort it
total: 31.85s
kind: proposal   seconds: [6.56, 25.28]   folder: C:\Users\kerim\Projects\local-assistant\sandbox\Downloads\New folder1
text:
The folder contains a mix of files: homework solutions, lecture notes, grocery and dentist reminders, a Turkish bill, an old installer, and duplicate images. I will organize them by moving the documents to a subfolder named "Documents", the images to "Images", and the installer to "Installers". The duplicate image will be deleted.
blocked (1):
  {'action': 'delete', 'file': 'IMG_4411 (1).jpg', 'reason': 'Remove duplicate file.', 'blocked': 'file changed twice'}
preview (9 steps):
  MOVE    hw2_solution.pdf  ->  Documents\hw2_solution.pdf  - Organize homework solutions into a dedicated folder.
  MOVE    Lecture_07.pdf  ->  Documents\Lecture_07.pdf  - Organize lecture notes into a dedicated folder.
  MOVE    Lecture_08.pdf  ->  Documents\Lecture_08.pdf  - Organize lecture notes into a dedicated folder.
  MOVE    notes.txt  ->  Documents\notes.txt  - Organize personal notes into a dedicated folder.
  MOVE    scan_0031.pdf  ->  Documents\scan_0031.pdf  - Organize bills and documents into a dedicated folder.
  MOVE    IMG_4410.jpg  ->  Images\IMG_4410.jpg  - Organize images into a dedicated folder.
  MOVE    IMG_4411 (1).jpg  ->  Images\IMG_4411 (1).jpg  - Organize images into a dedicated folder.
  MOVE    IMG_4411.jpg  ->  Images\IMG_4411.jpg  - Organize images into a dedicated folder.
  MOVE    SteamSetup.exe  ->  Installers\SteamSetup.exe  - Organize installers into a dedicated folder.

Median step 1 (routing): 6.49s   median step 2 (decide): 24.34s
```

## Full output: apply + undo

```
﻿Model: qwen3.5:4b-q4_K_M   Base: C:\Users\kerim\Projects\local-assistant\sandbox

===== APPLY + UNDO on C:\Users\kerim\Projects\local-assistant\sandbox\Downloads\New folder1
before:
  IMG_4410.jpg
  IMG_4411 (1).jpg
  IMG_4411.jpg
  Lecture_07.pdf
  Lecture_08.pdf
  SteamSetup.exe
  hw2_solution.pdf
  notes.txt
  scan_0031.pdf
kind: explain   seconds: [1.32, 16.42]   folder: C:\Users\kerim\Projects\local-assistant\sandbox\Downloads\New folder1
text:
Here is what is in your **Downloads/New folder1**:

*   **hw2_solution.pdf**: A document containing solutions for MATH 201 Linear Algebra Homework 2.
*   **IMG_4410.jpg**: An image file.
*   **IMG_4411 (1).jpg**: A duplicate of `IMG_4411.jpg`.
*   **IMG_4411.jpg**: An image file.
*   **Lecture_07.pdf**: A document for CSE 301 Operating Systems Lecture 7 on Virtual memory and paging.
*   **Lecture_08.pdf**: A document for CSE 301 Operating Systems Lecture 8 on Page replacement algorithms.
*   **notes.txt**: A text file containing grocery items and a reminder to call the dentist.
*   **scan_0031.pdf**: A scanned invoice (FATURA) from Turkcell.
*   **SteamSetup.exe**: An installer for Steam.

Would you like me to organize these files? For example, I can move the lecture notes into a "Lectures" folder, delete the duplicate image, and perhaps create a "Documents" or "Invoices" folder for the other files.
blocked (0):
preview: (no steps)

(a) gave no steps, using (h) for the apply/undo test
kind: proposal   seconds: [6.6, 24.9]   folder: C:\Users\kerim\Projects\local-assistant\sandbox\Downloads\New folder1
text:
The folder contains a mix of files: homework solutions, lecture notes, grocery and dentist reminders, a Turkish bill, an old installer, and duplicate images. I will organize them by moving the documents to a subfolder named "Documents", the images to "Images", and the installer to "Installers". The duplicate image will be deleted.
blocked (1):
  {'action': 'delete', 'file': 'IMG_4411 (1).jpg', 'reason': 'Remove duplicate file.', 'blocked': 'file changed twice'}
preview (9 steps):
  MOVE    hw2_solution.pdf  ->  Documents\hw2_solution.pdf  - Organize homework solutions into a dedicated folder.
  MOVE    Lecture_07.pdf  ->  Documents\Lecture_07.pdf  - Organize lecture notes into a dedicated folder.
  MOVE    Lecture_08.pdf  ->  Documents\Lecture_08.pdf  - Organize lecture notes into a dedicated folder.
  MOVE    notes.txt  ->  Documents\notes.txt  - Organize personal notes into a dedicated folder.
  MOVE    scan_0031.pdf  ->  Documents\scan_0031.pdf  - Organize bills and documents into a dedicated folder.
  MOVE    IMG_4410.jpg  ->  Images\IMG_4410.jpg  - Organize images into a dedicated folder.
  MOVE    IMG_4411 (1).jpg  ->  Images\IMG_4411 (1).jpg  - Organize images into a dedicated folder.
  MOVE    IMG_4411.jpg  ->  Images\IMG_4411.jpg  - Organize images into a dedicated folder.
  MOVE    SteamSetup.exe  ->  Installers\SteamSetup.exe  - Organize installers into a dedicated folder.
applied, log: C:\Users\kerim\.local-assistant\undo\20261022-030245.json
after apply:
  Documents
  Documents\Lecture_07.pdf
  Documents\Lecture_08.pdf
  Documents\hw2_solution.pdf
  Documents\notes.txt
  Documents\scan_0031.pdf
  Images
  Images\IMG_4410.jpg
  Images\IMG_4411 (1).jpg
  Images\IMG_4411.jpg
  Installers
  Installers\SteamSetup.exe
undo: Moved 9 file(s) back.
after undo:
  IMG_4410.jpg
  IMG_4411 (1).jpg
  IMG_4411.jpg
  Lecture_07.pdf
  Lecture_08.pdf
  SteamSetup.exe
  hw2_solution.pdf
  notes.txt
  scan_0031.pdf
identical to before: True   missing: []   extra: []
```
