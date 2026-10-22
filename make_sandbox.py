"""Creates a fake, messy set of folders to test on, so you never test on your real files.

    python make_sandbox.py            # creates sandbox/Downloads, sandbox/Downloads/New folder1, sandbox/Desktop
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


def main():
    if ROOT.exists():
        shutil.rmtree(ROOT)
    random.seed(1)
    now = time.time()
    for folder, name, content, age in FILES:
        path = ROOT / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            path.write_bytes(random.randbytes(random.randint(2_000, 60_000)))
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
    print(f"Created {len(FILES)} files under {ROOT.resolve()}")


if __name__ == "__main__":
    main()
