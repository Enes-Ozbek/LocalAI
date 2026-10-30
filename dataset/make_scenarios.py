"""Generates fake folder scenarios and user requests: the inputs of the training data.

    python dataset/make_scenarios.py --scenarios 300 --requests 4

Writes dataset/tasks.jsonl: one line per (folder scenario, request). The model only ever
sees fact sheets, so no real files are created. draft_answers.py then adds the answers.

To use real documents instead (for example from Govdocs1), replace the text_start values
with text extracted from those files; keep the rest of the format.
"""

import argparse
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant.scanner import type_of  # noqa: E402

HERE = Path(__file__).resolve().parent
TODAY = date.today()

COURSES = [
    ("CSE 301", "Operating Systems", ["Processes and threads", "CPU scheduling", "Virtual memory", "Page replacement", "File systems"]),
    ("CSE 302", "Database Systems", ["Relational model", "SQL queries", "Normalization", "Transactions", "Indexing"]),
    ("CSE 303", "Computer Networks", ["OSI model", "TCP and UDP", "Routing", "DNS", "Network security"]),
    ("MATH 201", "Linear Algebra", ["Matrices", "Determinants", "Vector spaces", "Eigenvalues", "Diagonalization"]),
    ("MATH 102", "Calculus II", ["Integration by parts", "Trigonometric integrals", "Series", "Taylor series", "Polar coordinates"]),
    ("PHYS 102", "Electricity and Magnetism", ["Coulomb's law", "Electric fields", "Gauss's law", "Capacitance", "Magnetic fields"]),
    ("SWE 210", "Software Testing", ["Unit testing", "Test coverage", "Mocking", "Integration testing", "TDD"]),
    ("ECON 101", "Introduction to Economics", ["Supply and demand", "Elasticity", "Market structures", "GDP", "Inflation"]),
]
BILLERS = [
    ("Turkcell Iletisim Hizmetleri A.S.", "phone bill", "Turkcell"),
    ("Vodafone Net", "internet bill", "Vodafone"),
    ("Turk Telekom", "internet bill", "TurkTelekom"),
    ("CK Bogazici Elektrik", "electricity bill", "Electricity"),
    ("IGDAS", "gas bill", "Gas"),
    ("ISKI", "water bill", "Water"),
]
INSTALLERS = ["ChromeSetup.exe", "SteamSetup.exe", "DiscordSetup.exe", "vlc-3.0.21-win64.exe",
              "python-3.12.4-amd64.exe", "Zoom_Installer.exe", "npp.8.6.9.Installer.x64.exe",
              "VSCodeUserSetup-x64-1.93.1.exe", "EpicInstaller-15.17.1.msi", "WinRAR-x64-701.exe",
              "OBS-Studio-30.2-Windows-Installer.exe", "spotify_installer.exe"]
FIRST_NAMES = ["Enes", "Kaan", "Ayse", "Mehmet", "Zeynep", "Can", "Elif", "Burak"]


def days_ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


def sheet(name, _type_hint, size_kb, age, text="", taken=""):
    # The type always comes from the extension, exactly like the app's scanner does it.
    s = {"file": name, "type": type_of(Path(name)), "size_kb": round(size_kb, 1), "modified": days_ago(age), "age_days": age}
    if taken:
        s["taken"] = taken
    if text:
        s["text_start"] = text[:160]
    return s


# --------------------------------------------------------------- file makers

def lecture(r):
    code, name, topics = r.choice(COURSES)
    n = r.randint(1, 12)
    fname = r.choice([f"Lecture_{n:02}.pdf", f"week{n}.pdf", f"lecture{n}.pdf",
                      f"{code.replace(' ', '')}_week{n}.pdf", f"{name.split()[0]}_slides_{n}.pptx", f"ders{n}.pdf"])
    type_ = "presentations" if fname.endswith(".pptx") else "documents"
    return sheet(fname, type_, r.uniform(300, 4000), r.randint(3, 200),
                 f"{code} {name} - Lecture {n}: {r.choice(topics)}")


def homework(r):
    code, name, _ = r.choice(COURSES)
    n = r.randint(1, 6)
    fname = r.choice([f"hw{n}.pdf", f"hw{n}_solution.pdf", f"assignment{n}.docx", f"odev{n}.pdf", f"HW{n}_{FIRST_NAMES[0]}.docx"])
    return sheet(fname, "documents", r.uniform(40, 900), r.randint(3, 150),
                 f"{code} {name} Homework {n}" + r.choice(["", " Solutions", " - due next week", " Student no: 2309000000"]))


def bill(r):
    company, kind, short = r.choice(BILLERS)
    age = r.randint(5, 400)
    d = TODAY - timedelta(days=age + 2)
    fname = r.choice([f"scan_{r.randint(1, 999):04}.pdf", f"document({r.randint(1, 9)}).pdf",
                      f"fatura_{d:%Y%m}.pdf", f"invoice_{r.randint(10000, 99999)}.pdf", f"{short}_{d:%Y-%m}.pdf"])
    return sheet(fname, "documents", r.uniform(80, 700), age,
                 f"FATURA {company} Fatura tarihi {d:%d.%m.%Y} ({kind}) Toplam {r.uniform(80, 900):.2f} TL")


def cv(r):
    name, year = r.choice(FIRST_NAMES), TODAY.year - r.randint(0, 2)
    fname = r.choice([f"CV_{name}_{year}.pdf", "resume.docx", f"{name}_CV.pdf", f"CV_{name}_{year}_updated.docx"])
    return sheet(fname, "documents", r.uniform(60, 400), r.randint(10, 800),
                 f"Curriculum Vitae {name} - Education: Computer Engineering. Skills: Python, Java, SQL")


def contract(r):
    text, fname = r.choice([
        ("Rental agreement between landlord and tenant, Istanbul, monthly rent", "kira_sozlesmesi.pdf"),
        ("Internship agreement between the student and the company, summer term", "internship_agreement.pdf"),
        ("Dormitory rules and quiet hours", "random.pdf"),
        ("Student certificate - this is to certify that the student is enrolled", "ogrenci_belgesi.pdf"),
    ])
    return sheet(r.choice([fname, f"document({r.randint(1, 9)}).pdf", f"scan_{r.randint(1, 999):04}.pdf"]),
                 "documents", r.uniform(100, 900), r.randint(10, 500), text)


def photo(r):
    age = r.randint(1, 900)
    d = TODAY - timedelta(days=age)
    t = f"{r.randint(8, 23):02}{r.randint(0, 59):02}{r.randint(0, 59):02}"
    fname = r.choice([f"IMG_{r.randint(1000, 9999)}.jpg", f"IMG_{d:%Y%m%d}_{t}.jpg",
                      f"WhatsApp Image {d:%Y-%m-%d} at {t[:2]}.{t[2:4]}.{t[4:]}.jpeg",
                      f"Screenshot {d:%Y-%m-%d} {t}.png", f"photo_{r.randint(1, 99)}.jpg"])
    taken = d.isoformat() if fname.startswith(("IMG", "photo")) and r.random() < 0.7 else ""
    return sheet(fname, "images", r.uniform(150, 5000), age, taken=taken)


def video(r):
    age = r.randint(1, 700)
    d = TODAY - timedelta(days=age)
    return sheet(r.choice([f"VID_{d:%Y%m%d}_{r.randint(100000, 235959)}.mp4", "video.mp4", f"recording_{d:%Y%m%d}.mkv"]),
                 "videos", r.uniform(5000, 900000), age)


def installer(r):
    return sheet(r.choice(INSTALLERS), "installers", r.uniform(1500, 250000), r.randint(20, 700))


def temp(r):
    return sheet(r.choice(["download.tmp", f"Unconfirmed {r.randint(10000, 99999)}.crdownload",
                           f"file_{r.randint(1, 99)}.part", "New Text Document.txt"]),
                 "other", r.uniform(0, 300), r.randint(1, 120))


def report_versions(r):
    base = r.choice(["report", "Capstone_proposal", "project_report", "thesis", "essay"])
    ext = r.choice([".docx", ".pdf"])
    age = r.randint(5, 120)
    out = []
    for i, suffix in enumerate(r.sample(["_v1", "_v2", "_v3", "_final", "_final_REAL", "_draft"], r.randint(2, 4))):
        out.append(sheet(f"{base}{suffix}{ext}", "documents", r.uniform(40, 900), age - i * r.randint(1, 5) if age > 20 else age,
                         f"{base.replace('_', ' ').title()} {suffix.strip('_')} version. Introduction ..."))
    return out


def spreadsheet(r):
    fname, text = r.choice([("budget_2026.xlsx", ""), ("expenses.csv", "date,item,amount 2026-09-01,rent,8000"),
                            ("grades.xlsx", ""), ("Book1.xlsx", ""), ("timetable.xlsx", "")])
    return sheet(fname, "spreadsheets", r.uniform(8, 300), r.randint(2, 300), text)


def note(r):
    fname, text = r.choice([("notes.txt", "groceries: milk, eggs, bread. call dentist friday"),
                            ("todo.txt", "1. finish lab report 2. email professor 3. pay internet bill"),
                            ("passwords_old.txt", ""), ("ideas.md", "Capstone ideas: local AI assistant, queue system"),
                            ("New Text Document (2).txt", "")])
    return sheet(fname, "documents", r.uniform(0, 20), r.randint(1, 300), text)


def work(r):
    fname, type_, text = r.choice([
        ("Q3_report.pptx", "presentations", "Q3 2026 Sales Report - Regional results"),
        (f"meeting_notes_{days_ago(r.randint(5, 90))}.docx", "documents", "Meeting notes - attendees, action items"),
        ("budget_forecast.xlsx", "spreadsheets", ""),
        ("contract_signed.pdf", "documents", "Service agreement signed by both parties"),
        ("offer_letter.pdf", "documents", "We are pleased to offer you the position of Junior Developer"),
        ("timesheet_october.xlsx", "spreadsheets", ""),
    ])
    return sheet(fname, type_, r.uniform(20, 3000), r.randint(2, 400), text)


def game(r):
    fname, type_ = r.choice([("mods.zip", "archives"), ("savegame_backup.zip", "archives"),
                             ("OptiFine_HD_U_I6.jar", "other"), ("ReShade_Setup_6.2.0.exe", "installers"),
                             ("minecraft_screenshot.png", "images"), ("texture_pack.zip", "archives")])
    return sheet(fname, type_, r.uniform(50, 400000), r.randint(5, 600))


def archive(r):
    return sheet(r.choice(["archive.zip", "photos_backup.zip", "assignment3_submission.zip", "project.rar",
                           f"Downloads_{r.randint(1, 9)}.7z"]), "archives", r.uniform(500, 900000), r.randint(10, 700))


PERSONAS = {
    "student": ({lecture: 5, homework: 3, bill: 1, cv: 1, contract: 1, photo: 2, installer: 2, temp: 1,
                 report_versions: 1, note: 1, spreadsheet: 1, archive: 1},
                ["Downloads", "Downloads/New folder1", "Desktop", "Documents/Uni", "Desktop/new folder", "Documents"]),
    "office": ({work: 5, bill: 2, cv: 1, contract: 1, report_versions: 1, spreadsheet: 2, installer: 1, temp: 1, note: 1},
               ["Downloads", "Desktop", "Documents/Work", "Documents"]),
    "phone_photos": ({photo: 8, video: 2},
                     ["Pictures/Phone", "Pictures", "Downloads/New folder", "Desktop/photos"]),
    "gamer": ({game: 4, installer: 3, photo: 2, video: 1, temp: 1, archive: 1, note: 1},
              ["Downloads", "Desktop"]),
    "mixed": ({lecture: 1, bill: 2, photo: 2, installer: 2, temp: 1, cv: 1, contract: 1, spreadsheet: 1,
               note: 1, archive: 1, work: 1, video: 1},
              ["Downloads", "Desktop", "Downloads/New folder1", "Documents"]),
}
EXISTING_SUBFOLDERS = ["University", "Bills", "Photos", "Work", "Installers", "Old", "Personal", "Screenshots"]


def make_scenario(r, sid):
    persona = r.choice(list(PERSONAS))
    makers, folders = PERSONAS[persona]
    folder = r.choice(folders)
    files, names = [], set()
    for _ in range(r.randint(5, 28)):
        maker = r.choices(list(makers), weights=list(makers.values()))[0]
        for s in (maker(r) if maker is report_versions else [maker(r)]):
            if s["file"] not in names:
                names.add(s["file"])
                files.append(s)
    # Exact duplicates, named the way Windows and browsers name copies.
    for s in r.sample(files, k=min(len(files), r.choice([0, 0, 1, 1, 2]))):
        stem, dot, ext = s["file"].rpartition(".")
        copy_name = f"{stem} (1).{ext}" if dot else f"{s['file']} (1)"
        if copy_name not in names:
            dup = dict(s, file=copy_name, duplicate_of=s["file"],
                       age_days=max(0, s["age_days"] - 1), modified=days_ago(max(0, s["age_days"] - 1)))
            names.add(copy_name)
            files.append(dup)
    files.sort(key=lambda s: s["file"].lower())
    subfolders = sorted(r.sample(EXISTING_SUBFOLDERS, k=r.choice([0, 0, 1, 2])))
    return {"scenario_id": sid, "persona": persona, "folder": folder,
            "facts": {"folder": folder, "subfolders": subfolders, "files": files[:40]}}


# --------------------------------------------------------------- requests

REQUESTS = {
    "inspect_explain_organize": (35, [
        "check files in my {f} and tell me what those are then organise them",
        "what's in {f}? tell me and then sort it out",
        "look at {f}, explain what the files are and organize them",
        "can you see whats inside {f} and clean it up",
        "go through {f}, tell me what everything is, then tidy it",
        "check {f} and organise it, but first tell me what you found"]),
    "organize": (20, [
        "organize my {f}", "tidy up {f}", "clean up {f} please", "{f} is a mess, fix it",
        "sort out {f}", "organise {f}", "can you organize the files in {f}?", "make {f} clean"]),
    "organize_limit": (15, [
        "organize {f} but don't delete anything", "clean up {f} without deleting files",
        "sort {f}, don't remove anything", "only organize the pdfs in {f}",
        "organize {f} but leave the photos where they are", "tidy {f} but keep the installers"]),
    "organize_rule": (10, [
        "sort {f} by file type", "put the files in {f} into folders by year", "group {f} by month",
        "organize {f} by topic", "organize {f} by course"]),
    "explain_only": (10, [
        "what's in {f}? don't change anything", "just tell me what's in {f}",
        "what kind of files are in {f}?", "describe the files in {f}, don't move anything"]),
    "clarify": (5, [
        "organize that folder", "clean it up", "sort the folder from yesterday", "delete the old ones",
        "organise my files", "fix the mess"]),
    "refuse": (5, [
        "organize C:\\Windows\\System32", "clean up Program Files", "delete everything in C:\\Windows",
        "what's the weather tomorrow", "write my homework for me", "organize my AppData folder"]),
    # One file the user names ({file}), and what to do with it
    "one_file": (10, [
        "delete {file} from {f}", "remove {file} in {f}", "can you delete {file} in {f}",
        "move {file} in {f} into a folder called {sub}", "put {file} from {f} in a {sub} folder",
        "rename {file} in {f} to {new}", "in {f}, rename {file} to {new}"]),
    "move_elsewhere": (5, [  # to another of the user's folders ({top}), not a subfolder
        "move {file} from {f} to {top}", "move {file} to {top} from {f}", "put {file} from {f} into my {top} folder"]),
}
NEW_SUBFOLDERS = ["Important", "To print", "Archive", "Keep", "Old", "University", "Bills", "Work"]
NEW_SUFFIXES = ["final", "old", "backup", "keep", "important", "to_print", "2026"]


def one_file(r, scenario, template):
    """Fills {file}, {sub}, {new} and {top} for a request about one named file."""
    files = [s for s in scenario["facts"]["files"] if "duplicate_of" not in s]
    s = r.choice(files)
    name = s["file"]
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    said = name
    if len(stem) >= 5 and not stem.isalpha() and r.random() < 0.25:
        said = stem  # 'move report_final to documents': the app matches a name without its extension too
    new = f"{stem}_{r.choice(NEW_SUFFIXES)}" + (f".{ext}" if dot else "")
    top_folder = scenario["folder"].split("/")[0]
    tops = [t for t in (["Pictures", "Desktop"] if s["type"] == "images" else ["Documents", "Desktop"]) if t != top_folder]
    return (template.replace("{file}", said).replace("{sub}", r.choice(NEW_SUBFOLDERS))
            .replace("{new}", new).replace("{top}", r.choice(tops) if r.random() < 0.6 else r.choice(tops).lower()))


def folder_mention(r, folder):
    words = folder
    if r.random() < 0.4:
        words = words.lower()
    if r.random() < 0.3:
        words = "my " + words if not words.lower().startswith("my") else words
    return words


def make_requests(r, scenario, k):
    types = list(REQUESTS)
    weights = [REQUESTS[t][0] for t in types]
    out = []
    for i, rtype in enumerate(r.choices(types, weights=weights, k=k)):
        template = r.choice(REQUESTS[rtype][1])
        if rtype == "organize_rule" and "course" in template and scenario["persona"] != "student":
            template = "sort {f} by file type"
        if rtype in ("one_file", "move_elsewhere"):
            template = one_file(r, scenario, template)
        text = template.replace("{f}", folder_mention(r, scenario["folder"])).replace("my my", "my")
        if r.random() < 0.15 and rtype not in ("one_file", "move_elsewhere"):  # some typos, like real users
            text = text.replace("organize", "organzie").replace("files", "fiels")
        out.append({"task_id": f"{scenario['scenario_id']}-{i}", "request_type": rtype, "user": text})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", type=int, default=300)
    ap.add_argument("--requests", type=int, default=4, help="requests per scenario")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(HERE / "tasks.jsonl"))
    args = ap.parse_args()
    r = random.Random(args.seed)
    n = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for i in range(args.scenarios):
            sc = make_scenario(r, f"s{i:04}")
            for req in make_requests(r, sc, args.requests):
                out.write(json.dumps({**sc, **req}, ensure_ascii=False) + "\n")
                n += 1
    print(f"Wrote {n} tasks from {args.scenarios} scenarios to {args.out}")


if __name__ == "__main__":
    main()
