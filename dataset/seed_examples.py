"""Hand-written seed examples for the folder-organizing dataset.

    python dataset/seed_examples.py      # writes dataset/seed_examples.jsonl

Each record is one full conversation in the chat format used for fine-tuning:
user request -> inspect_folder -> fact sheets (tool result) -> explanation + propose_changes.
Copy the patterns here when you write new examples; every record must pass validate.py.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant.schema import SYSTEM_PROMPT, tool_result  # noqa: E402

HERE = Path(__file__).resolve().parent


def call(name, **args):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"type": "function", "function": {"name": name, "arguments": args}}]}


def answer(text, name=None, **args):
    msg = {"role": "assistant", "content": text}
    if name:
        msg["tool_calls"] = [{"type": "function", "function": {"name": name, "arguments": args}}]
    return msg


def result(folder, sheets, subfolders=()):
    facts = {"folder": folder, "subfolders": list(subfolders), "files": sheets}
    return {"role": "tool", "tool_name": "inspect_folder", "content": tool_result(facts)}


def f(file, type_, size_kb, modified, age_days, text="", dup=""):
    s = {"file": file, "type": type_, "size_kb": size_kb, "modified": modified, "age_days": age_days}
    if dup:
        s["duplicate_of"] = dup
    if text:
        s["text_start"] = text
    return s


def change(file, action, reason, to_folder="", new_name=""):
    c = {"file": file, "action": action}
    if to_folder:
        c["to_folder"] = to_folder
    if new_name:
        c["new_name"] = new_name
    c["reason"] = reason
    return c


def record(user, *steps, kind, tags=()):
    """kind: the request type, as in make_scenarios.py (used by evaluate.py)."""
    return {"meta": {"task_id": "", "scenario_id": "seed", "request_type": kind},
            "tags": list(tags), "source": "seed",
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": user}, *steps]}


# ---------------------------------------------------------------------------
# 1. Enes's example: inspect, explain, organize.
NEW_FOLDER1 = [
    f("Lecture_07.pdf", "documents", 2310.4, "2026-10-08", 13, "CSE 301 Operating Systems - Lecture 7: Virtual memory, paging"),
    f("Lecture_08.pdf", "documents", 1987.0, "2026-10-15", 6, "CSE 301 Operating Systems - Lecture 8: Page replacement algorithms"),
    f("hw2_solution.pdf", "documents", 410.2, "2026-10-12", 9, "MATH 201 Linear Algebra Homework 2 Solutions"),
    f("scan_0031.pdf", "documents", 520.7, "2026-09-30", 21, "FATURA Turkcell Iletisim Hizmetleri A.S. Fatura tarihi 28.09.2026 Toplam 349,90 TL"),
    f("IMG_4410.jpg", "images", 3120.0, "2026-09-02", 49),
    f("IMG_4411.jpg", "images", 2988.5, "2026-09-02", 49),
    f("IMG_4411 (1).jpg", "images", 2988.5, "2026-09-03", 48, dup="IMG_4411.jpg"),
    f("SteamSetup.exe", "installers", 2350.1, "2025-11-20", 335),
    f("notes.txt", "documents", 1.2, "2026-10-18", 3, "groceries: milk, eggs, bread. call dentist friday"),
]

EX1 = record(
    "check files in my downloads/new folder1 and tell me what those are then organise them",
    call("inspect_folder", folder="Downloads/New folder1"),
    result("Downloads/New folder1", NEW_FOLDER1),
    answer(
        "This folder has 9 files:\n"
        "- 2 Operating Systems lecture slides (CSE 301, lectures 7 and 8)\n"
        "- 1 Linear Algebra homework solution (MATH 201)\n"
        "- 1 Turkcell phone bill from September 2026 (scan_0031.pdf)\n"
        "- 3 photos from 2 September 2026; IMG_4411 (1).jpg is an exact copy of IMG_4411.jpg\n"
        "- 1 Steam installer from almost a year ago\n"
        "- 1 short personal to-do note\n"
        "I suggest the changes below. Nothing happens until you confirm.",
        "propose_changes", folder="Downloads/New folder1", changes=[
            change("Lecture_07.pdf", "move", "Operating Systems lecture slides", "University/Operating Systems"),
            change("Lecture_08.pdf", "move", "Operating Systems lecture slides", "University/Operating Systems"),
            change("hw2_solution.pdf", "move", "Linear Algebra homework", "University/Linear Algebra",
                   "Linear_Algebra_HW2_solution.pdf"),
            change("scan_0031.pdf", "move", "Phone bill; the scanner name says nothing", "Bills",
                   "Turkcell_bill_2026-09.pdf"),
            change("IMG_4410.jpg", "move", "Photo from 2 Sep 2026", "Photos/2026-09"),
            change("IMG_4411.jpg", "move", "Photo from 2 Sep 2026", "Photos/2026-09"),
            change("IMG_4411 (1).jpg", "delete", "Exact duplicate of IMG_4411.jpg"),
            change("SteamSetup.exe", "delete", "Old installer; Steam can be downloaded again"),
        ]),
    kind="inspect_explain_organize", tags=["inspect+explain+organize", "duplicates", "rename-by-content"],
)

# 2. Explain only: the user does not want changes.
DESKTOP = [
    f("Capstone_proposal_v3.docx", "documents", 88.0, "2026-10-16", 5, "Capstone Project Proposal Fall 2026 Privacy-First Local AI Assistant"),
    f("Capstone_proposal_v2.docx", "documents", 86.4, "2026-10-14", 7, "Capstone Project Proposal Fall 2026 Privacy-First Local AI Assistant"),
    f("Screenshot 2026-10-19 221503.png", "images", 412.0, "2026-10-19", 2),
    f("budget_2026.xlsx", "spreadsheets", 22.5, "2026-10-01", 20),
    f("Discord.lnk", "other", 1.4, "2026-03-11", 224),
]

EX2 = record(
    "what's on my desktop? just tell me, don't change anything",
    call("inspect_folder", folder="Desktop"),
    result("Desktop", DESKTOP),
    answer(
        "Your desktop has 5 items:\n"
        "- Two versions of your capstone proposal (v2 from 14 Oct and v3 from 16 Oct)\n"
        "- A screenshot from 19 Oct\n"
        "- Your 2026 budget spreadsheet\n"
        "- A shortcut to Discord\n"
        "I didn't change anything. If you want, I can move the older proposal version into a Drafts folder."),
    kind="explain_only", tags=["explain-only", "no-changes"],
)

# 3. Organize with a limit: no deleting.
EX3 = record(
    "organise downloads/new folder1 but don't delete anything",
    call("inspect_folder", folder="Downloads/New folder1"),
    result("Downloads/New folder1", NEW_FOLDER1),
    answer(
        "I grouped the files by what they are and deleted nothing. The duplicate photo and the old "
        "Steam installer go into a 'To check' folder so you can decide later.",
        "propose_changes", folder="Downloads/New folder1", changes=[
            change("Lecture_07.pdf", "move", "Operating Systems lecture slides", "University/Operating Systems"),
            change("Lecture_08.pdf", "move", "Operating Systems lecture slides", "University/Operating Systems"),
            change("hw2_solution.pdf", "move", "Linear Algebra homework", "University/Linear Algebra"),
            change("scan_0031.pdf", "move", "Phone bill", "Bills", "Turkcell_bill_2026-09.pdf"),
            change("IMG_4410.jpg", "move", "Photo from 2 Sep 2026", "Photos/2026-09"),
            change("IMG_4411.jpg", "move", "Photo from 2 Sep 2026", "Photos/2026-09"),
            change("IMG_4411 (1).jpg", "move", "Duplicate of IMG_4411.jpg; not deleted because you asked", "To check"),
            change("SteamSetup.exe", "move", "Old installer; not deleted because you asked", "To check"),
        ]),
    kind="organize_limit", tags=["organize", "constraint:no-delete"],
)

# 4. Organize by a rule the user gives (photos by year).
PHONE = [
    f("IMG_20240714_181022.jpg", "images", 3402.0, "2024-07-14", 829),
    f("IMG_20240714_181530.jpg", "images", 3377.1, "2024-07-14", 829),
    f("IMG_20251231_235901.jpg", "images", 2890.4, "2025-12-31", 294),
    f("IMG_20260101_000312.jpg", "images", 2911.0, "2026-01-01", 293),
    f("VID_20260520_140211.mp4", "videos", 48211.0, "2026-05-20", 154),
]

EX4 = record(
    "sort the photos in Pictures/Phone into folders by year",
    call("inspect_folder", folder="Pictures/Phone"),
    result("Pictures/Phone", PHONE),
    answer(
        "There are 4 photos and 1 video, from 2024 to 2026. I put each in a folder for its year, "
        "using the date in the file name. The video goes in its year too.",
        "propose_changes", folder="Pictures/Phone", changes=[
            change("IMG_20240714_181022.jpg", "move", "Taken 14 Jul 2024", "2024"),
            change("IMG_20240714_181530.jpg", "move", "Taken 14 Jul 2024", "2024"),
            change("IMG_20251231_235901.jpg", "move", "Taken 31 Dec 2025", "2025"),
            change("IMG_20260101_000312.jpg", "move", "Taken 1 Jan 2026", "2026"),
            change("VID_20260520_140211.mp4", "move", "Recorded 20 May 2026", "2026"),
        ]),
    kind="organize_rule", tags=["organize", "user-rule:by-year"],
)

# 5. Unclear which folder -> ask.
EX5 = record(
    "organise that folder from yesterday",
    answer("", "ask_clarification",
           question="Which folder do you mean? You can type its name or path, for example 'Downloads/New folder1'."),
    kind="clarify", tags=["clarify"],
)

# 6. System folder -> refuse (no inspect).
EX6 = record(
    "organise C:\\Windows\\System32, it's a mess",
    answer("", "refuse",
           reason="System32 is a Windows system folder. Moving or deleting files there can break Windows, so I won't change it."),
    kind="refuse", tags=["refuse", "safety"],
)

# 7. Group by topic using file contents.
UNI = [
    f("week1.pdf", "documents", 1200.0, "2026-09-22", 29, "PHYS 102 Electricity and Magnetism - Week 1: Coulomb's law"),
    f("week2.pdf", "documents", 1350.2, "2026-09-29", 22, "PHYS 102 Electricity and Magnetism - Week 2: Electric fields"),
    f("week1 (2).pdf", "documents", 980.4, "2026-09-23", 28, "CSE 302 Database Systems - Week 1: Relational model"),
    f("lab1.docx", "documents", 45.1, "2026-10-02", 19, "CSE 302 Lab 1: SQL SELECT queries. Student no: 2309000000"),
    f("syllabus.pdf", "documents", 210.0, "2026-09-20", 31, "PHYS 102 Syllabus Fall 2026 Grading: Midterm 30%"),
    f("random.pdf", "documents", 75.3, "2026-10-05", 16, "Dormitory rules and quiet hours"),
]

EX7 = record(
    "put my uni stuff in Documents/Uni into folders by course",
    call("inspect_folder", folder="Documents/Uni"),
    result("Documents/Uni", UNI, ["PHYS 102"]),
    answer(
        "I found files from two courses, PHYS 102 (Electricity and Magnetism) and CSE 302 (Database "
        "Systems), and one file that is not coursework (dormitory rules). The PHYS 102 files go into "
        "your existing PHYS 102 folder. Two files are both called "
        "'week1', so I renamed them to make the course clear.",
        "propose_changes", folder="Documents/Uni", changes=[
            change("week1.pdf", "move", "PHYS 102 lecture notes", "PHYS 102", "PHYS102_week1.pdf"),
            change("week2.pdf", "move", "PHYS 102 lecture notes", "PHYS 102", "PHYS102_week2.pdf"),
            change("syllabus.pdf", "move", "PHYS 102 syllabus", "PHYS 102", "PHYS102_syllabus.pdf"),
            change("week1 (2).pdf", "move", "CSE 302 lecture notes", "CSE 302", "CSE302_week1.pdf"),
            change("lab1.docx", "move", "CSE 302 lab work", "CSE 302", "CSE302_lab1.docx"),
            change("random.pdf", "rename", "Not coursework; clearer name, left in place",
                   new_name="Dormitory_rules.pdf"),
        ]),
    kind="organize_rule", tags=["organize", "group-by-topic", "rename-by-content"],
)

EXAMPLES = [EX1, EX2, EX3, EX4, EX5, EX6, EX7]
for n, ex in enumerate(EXAMPLES, 1):
    ex["meta"]["task_id"] = f"seed-{n}"

if __name__ == "__main__":
    with open(HERE / "seed_examples.jsonl", "w", encoding="utf-8") as out:
        for ex in EXAMPLES:
            out.write(json.dumps(ex, ensure_ascii=False) + "\n")
    print(f"Wrote {len(EXAMPLES)} examples to seed_examples.jsonl")
