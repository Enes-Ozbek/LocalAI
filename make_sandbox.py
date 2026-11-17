"""Creates a fake, messy set of folders to test on, so you never test on your real files.

    python make_sandbox.py            # creates sandbox/Downloads, sandbox/Downloads/New folder1, sandbox/Desktop,
                                      # sandbox/Archive (a big folder: 160 files for bulk jobs and search),
                                      # empty sandbox/Documents and sandbox/Pictures to move files into, and
                                      # sandbox/AppData + dot-folders with app leftovers (installed.json says
                                      # which programs this fake PC has; APPS says what each folder really is)
    python cli.py --base sandbox      # then 'Downloads' means sandbox/Downloads

Run it again any time to reset the sandbox.
"""

import os
import random
import shutil
import time
from pathlib import Path

ROOT = Path("sandbox")
DAY = 86400


def make_pdf(path: Path, text: str):
    """A minimal real PDF with one line of text (pypdf can read it back)."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 11 Tf 40 760 Td ({safe}) Tj ET".encode("latin-1", "replace")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010} 00000 n \n".encode() for o in offsets)
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


def make_docx(path: Path, text: str):
    try:
        import docx
        d = docx.Document()
        d.add_paragraph(text)
        d.save(path)
    except ImportError:
        path.write_bytes(b"PK")  # placeholder if python-docx is missing


# (folder, file name, content, age in days). Content: text, "dup:<name>", or None for random bytes.
FILES = [
    # Downloads/New folder1: the example from the project thread
    ("Downloads/New folder1", "Lecture_07.pdf", "CSE 301 Operating Systems - Lecture 7: Virtual memory, paging", 13),
    ("Downloads/New folder1", "Lecture_08.pdf", "CSE 301 Operating Systems - Lecture 8: Page replacement algorithms", 6),
    ("Downloads/New folder1", "hw2_solution.pdf", "MATH 201 Linear Algebra Homework 2 Solutions", 9),
    ("Downloads/New folder1", "scan_0031.pdf", "FATURA Turkcell Iletisim Hizmetleri A.S. Fatura tarihi 28.09.2026 Toplam 349,90 TL", 21),
    ("Downloads/New folder1", "IMG_4410.jpg", None, 49),
    ("Downloads/New folder1", "IMG_4411.jpg", None, 49),
    ("Downloads/New folder1", "IMG_4411 (1).jpg", "dup:IMG_4411.jpg", 48),
    ("Downloads/New folder1", "SteamSetup.exe", None, 335),
    ("Downloads/New folder1", "notes.txt", "groceries: milk, eggs, bread. call dentist friday", 3),
    # Downloads
    ("Downloads", "ChromeSetup.exe", None, 400),
    ("Downloads", "vlc-3.0.21-win64.exe", None, 300),
    ("Downloads", "python-3.12.4-amd64.exe", None, 250),
    ("Downloads", "Zoom_Installer (1).exe", None, 200),
    ("Downloads", "IMG_2034.jpg", None, 120),
    ("Downloads", "IMG_2034 (1).jpg", "dup:IMG_2034.jpg", 119),
    ("Downloads", "WhatsApp Image 2025-03-02 at 14.22.11.jpeg", None, 230),
    ("Downloads", "scan_0012.pdf", "FATURA Vodafone Net Fatura tarihi 14.03.2026 Toplam 449,00 TL", 220),
    ("Downloads", "document(3).pdf", "Rental agreement between landlord and tenant, Istanbul, starting 1 Sept 2025", 50),
    ("Downloads", "CV_Enes_2024.docx", "Curriculum Vitae - Education: Computer Engineering. Skills: Python, ML", 400),
    ("Downloads", "CV_Enes_2025.docx", "Curriculum Vitae (updated) - Education: Computer Engineering. Skills: Python, ML, LLMs", 60),
    ("Downloads", "calculus_week3_notes.pdf", "MATH 102 Calculus II - Week 3: integration by parts", 90),
    ("Downloads", "lecture5.pdf", "CSE 302 Data Structures - Lecture 5: binary search trees", 70),
    ("Downloads", "report_v1.docx", "Capstone report draft v1. Introduction", 40),
    ("Downloads", "report_final.docx", "Capstone report final version. Introduction", 20),
    ("Downloads", "download.tmp", None, 15),
    ("Downloads", "Unconfirmed 48213.crdownload", None, 45),
    ("Downloads", "budget.csv", "month,rent,food\n2026-01,8000,4000\n2026-02,8000,4200", 260),
    ("Downloads", "thesis_final_REAL.docx", "Final thesis text - DO NOT LOSE", 7),
    # Desktop
    ("Desktop", "Capstone_proposal_v2.docx", "Capstone Project Proposal Fall 2026 Privacy-First Local AI Assistant", 7),
    ("Desktop", "Capstone_proposal_v3.docx", "Capstone Project Proposal Fall 2026 Privacy-First Local AI Assistant (v3)", 5),
    ("Desktop", "Screenshot 2026-10-19 221503.png", None, 2),
    ("Desktop", "todo.txt", "1. finish lab report 2. email professor 3. pay internet bill", 1),
]


COURSES = ["CSE 301 Operating Systems", "CSE 302 Data Structures", "MATH 201 Linear Algebra", "PHYS 102 Physics II",
           "CSE 401 Machine Learning", "CSE 315 Databases"]
BILLS = ["FATURA Turkcell Iletisim Hizmetleri", "FATURA Vodafone Net", "FATURA IGDAS Dogalgaz", "FATURA CK Bogazici Elektrik"]


def archive_files() -> list:
    """A big, messy folder like years of downloads: photos (some copied twice), screenshots, lecture notes,
    bills, installers, unfinished downloads and a few old CVs. Same files every time."""
    r = random.Random(7)
    out = []
    for i in range(70):
        age = r.randint(3, 1100)
        out.append(("Archive", f"IMG_{3000 + i}.jpg", None, age))
        if i % 9 == 0:
            out.append(("Archive", f"IMG_{3000 + i} (1).jpg", f"dup:IMG_{3000 + i}.jpg", age - 1))
    for i in range(20):
        age = r.randint(1, 700)
        day = time.strftime("%Y-%m-%d", time.localtime(time.time() - age * DAY))
        out.append(("Archive", f"Screenshot {day} {r.randint(100000, 235959)}.png", None, age))
    for i in range(24):
        course = COURSES[i % len(COURSES)]
        out.append(("Archive", f"lecture_{i % len(COURSES) + 1:02}_{i // len(COURSES) + 1}.pdf",
                    f"{course} - Lecture {i // len(COURSES) + 1}", r.randint(20, 800)))
    for i in range(12):
        bill = BILLS[i % len(BILLS)]
        out.append(("Archive", f"scan_{100 + i:04}.pdf", f"{bill} Fatura tarihi {1 + i % 28:02}.{1 + i % 12:02}.2025 "
                    f"Toplam {r.randint(150, 900)},{r.randint(0, 99):02} TL", r.randint(30, 600)))
    for name in ("Discord_Setup.exe", "Spotify_Installer.exe", "node-v20.11.0-x64.msi", "Steam_Setup (1).exe",
                 "7z2301-x64.exe", "Git-2.44.0-64-bit.exe", "AnyDesk.exe", "OBS-Studio-30.0-Windows-Installer.exe"):
        out.append(("Archive", name, None, r.randint(60, 900)))
    for i in range(7):
        out.append(("Archive", f"download ({i}).tmp" if i % 2 else f"Unconfirmed {r.randint(10000, 99999)}.crdownload",
                    None, r.randint(2, 300)))
    out += [("Archive", "Resume_Kaan_2023.docx", "Resume - Kaan. Experience: intern at a software company. "
             "Education: Computer Engineering", 600),
            ("Archive", "CV_old_2022.docx", "Curriculum Vitae 2022 - Enes. Education: high school, Computer Engineering",
             900),
            ("Archive", "rental_contract_2024.pdf", "Rental agreement between landlord and tenant, Kadikoy, 2024", 500),
            ("Archive", "bank_statement_2025_06.pdf", "Account statement June 2025, Ziraat Bankasi", 140),
            ("Archive", "tax_return_2024.pdf", "Income tax return 2024 - declaration", 300),
            ("Archive", "meeting_notes.txt", "capstone meeting: split the work, Kaan does the UI, Enes the model", 8),
            ("Archive", "shopping_list.txt", "milk, bread, coffee", 40),
            ("Archive", "trip_istanbul_2023.zip", None, 700), ("Archive", "old_projects.zip", None, 650),
            ("Archive", "holiday_video_2024.mp4", None, 400), ("Archive", "podcast_ep12.mp3", None, 200)]
    return out


K = 1024
# Leftovers of uninstalled apps: (app folder, [(file inside, bytes, age in days)], what it really is).
# What it is: leftover, cache, ask (the user's own files), keep (an installed program's) or never (Windows').
# bench/leftovers.py scores the model against it.
APPS = [
    ("AppData/Roaming/ollama app.exe", [("config.json", 2 * K, 3), ("app.log", 40 * K, 1)], "leftover"),
    ("AppData/Local/Ollama", [("server.log", 300 * K, 2), ("upgrade.log", 8 * K, 30)], "leftover"),
    (".ollama", [("id_ed25519.pub", 100, 90), ("models/blobs/sha256-6a0746a1", 900 * K, 40),
                 ("models/manifests/registry/library/qwen3.5/4b", 1 * K, 40)], "leftover"),
    ("AppData/Local/Docker", [("wsl/disk/docker_data.vhdx", 1500 * K, 95), ("log/host/com.docker.backend.log", 60 * K, 95)],
     "leftover"),
    ("AppData/Roaming/Docker", [("settings-store.json", 3 * K, 95)], "leftover"),
    ("AppData/Local/Origin", [("Origin/local.xml", 4 * K, 420), ("Logs/Client_Log.txt", 70 * K, 420)], "leftover"),
    ("AppData/Local/Razer", [("Synapse3/Log/Razer Synapse 3.log", 120 * K, 300), ("Synapse3/Settings/config.json", 2 * K, 300)],
     "leftover"),
    ("AppData/Roaming/Wireshark", [("preferences", 6 * K, 510), ("recent_common", 2 * K, 510)], "leftover"),
    ("AppData/Local/Overwolf", [("Log/OverwolfLauncher.log", 30 * K, 130), ("Extensions/x/manifest.json", 3 * K, 130)],
     "leftover"),
    ("AppData/Local/QuickNote", [("settings.ini", 1 * K, 4), ("logs/today.log", 9 * K, 4)], "leftover"),  # recent
    ("AppData/Roaming/EldenRing", [("76561198000000000/ER0000.sl2", 600 * K, 200), ("GraphicsConfig.xml", 2 * K, 200)],
     "ask"),
    (".minecraft", [("saves/World1/level.dat", 50 * K, 150), ("options.txt", 4 * K, 150)], "ask"),
    ("AppData/Local/Discord", [("app-1.0.9/Discord.exe", 700 * K, 6), ("settings.json", 1 * K, 1)], "keep"),
    ("AppData/Roaming/discord", [("Cache/Cache_Data/data_1", 400 * K, 1), ("Code Cache/js/index", 200 * K, 1),
                                 ("settings.json", 1 * K, 2)], "cache"),
    ("AppData/Local/npm-cache", [("_cacache/content-v2/sha512/ab/cd", 500 * K, 20), ("_logs/debug-0.log", 10 * K, 20)],
     "cache"),
    ("AppData/Local/pip", [("cache/http-v2/a/b/c", 300 * K, 12)], "cache"),
    ("AppData/Roaming/Code", [("User/settings.json", 2 * K, 3), ("Cache/Cache_Data/f_000001", 100 * K, 3)], "keep"),
    (".vscode", [("extensions/ms-python.python-2026.1.0/package.json", 20 * K, 15)], "keep"),
    ("AppData/Local/Steam", [("htmlcache/Cache/data_0", 200 * K, 2)], "keep"),
    ("AppData/Local/Programs/Microsoft VS Code", [("Code.exe", 900 * K, 30)], "keep"),
    ("AppData/Local/Microsoft", [("Windows/UsrClass.dat", 300 * K, 0)], "never"),
    ("AppData/Local/Temp", [("tmp1234.tmp", 50 * K, 1)], "never"),
]
INSTALLED = {
    "programs": [{"name": "Discord", "publisher": "Discord Inc.", "location": "AppData/Local/Discord"},
                 {"name": "Steam", "publisher": "Valve Corporation", "location": "Program Files (x86)/Steam"},
                 {"name": "Microsoft Visual Studio Code (User)", "publisher": "Microsoft Corporation",
                  "location": "AppData/Local/Programs/Microsoft VS Code"},
                 {"name": "Node.js", "publisher": "Node.js Foundation", "location": "Program Files/nodejs"},
                 {"name": "Python 3.12.4 (64-bit)", "publisher": "Python Software Foundation"},
                 {"name": "Git", "publisher": "The Git Development Community"},
                 {"name": "7-Zip 23.01 (x64)", "publisher": "Igor Pavlov"}],
    "running": ["AppData/Local/Discord/app-1.0.9/Discord.exe"],
}


def make_apps(root: Path, now: float):
    import json
    for folder, files, _ in APPS:
        for rel, size, age in files:
            path = root / folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(random.randbytes(size))
            t = now - age * DAY
            os.utime(path, (t, t))
    for folder, files, _ in APPS:  # folders get the time of their newest file, as on a real PC
        newest = now - min(age for _, _, age in files) * DAY
        for d in sorted({(root / folder / rel).parent for rel, _, _ in files} | {root / folder}, key=lambda p: -len(p.parts)):
            os.utime(d, (newest, newest))
    (root / "installed.json").write_text(json.dumps(INSTALLED, indent=1), encoding="utf-8")


def main():
    if ROOT.exists():
        shutil.rmtree(ROOT)
    random.seed(1)
    now = time.time()
    for folder, name, content, age in FILES + archive_files():
        path = ROOT / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            path.write_bytes(random.randbytes(random.randint(2_000, 60_000) if folder != "Archive" else
                                              random.randint(1_000, 12_000)))
        elif content.startswith("dup:"):
            path.write_bytes((path.parent / content[4:]).read_bytes())
        elif name.endswith(".pdf"):
            make_pdf(path, content)
        elif name.endswith(".docx"):
            make_docx(path, content)
        else:
            path.write_text(content, encoding="utf-8")
        t = now - age * DAY
        os.utime(path, (t, t))
    for empty in ("Documents", "Pictures"):
        (ROOT / empty).mkdir()
    make_apps(ROOT, now)
    print(f"Created {len(FILES) + len(archive_files())} files and {len(APPS)} app folders under {ROOT.resolve()}")


if __name__ == "__main__":
    main()
