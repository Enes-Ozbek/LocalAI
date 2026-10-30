"""Puts the project code into colab/finetune_kaggle.ipynb, because on Kaggle there is no upload button
for local-assistant.zip like Colab's. Run it after changing any code, before using the Kaggle notebook:

    python colab/embed_code.py
"""

import base64
import io
import json
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK = ROOT / "colab" / "finetune_kaggle.ipynb"
SKIP_DIRS = {"colab", "sandbox", "results", "models", ".venv", "__pycache__", ".git", ".github", ".pytest_cache", ".pytest_tmp", "docs", "tests"}
MARK = "# Step 0:"


def wanted(path: Path) -> bool:
    """Code and the hand-written examples, not data made by earlier runs (the notebook takes those from
    your progress zip)."""
    rel = path.relative_to(ROOT)
    if path.name.startswith("eval_") or path.suffix in (".pyc", ".zip", ".gguf", ".log"):
        return False
    return not (rel.parts[0] == "dataset" and path.suffix == ".jsonl" and path.name != "seed_examples.jsonl")


def code_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, dirs, names in os.walk(ROOT):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for path in sorted(Path(folder) / n for n in names):
                if wanted(path):
                    z.write(path, Path("local-assistant") / path.relative_to(ROOT))
    return buf.getvalue()


def main():
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    cell = next(c for c in nb["cells"] if "".join(c["source"]).startswith(MARK))
    data = code_zip()
    source = (f"{MARK} the project code ({len(data) // 1024} KB zip), put here by colab/embed_code.py. Just run it.\n"
              'CODE_ZIP = """\n' + base64.encodebytes(data).decode() + '"""\n')
    cell["source"] = source.splitlines(keepends=True)
    NOTEBOOK.write_text(json.dumps(nb, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        print(f"{NOTEBOOK.name}: {len(z.namelist())} files embedded ({len(data) // 1024} KB)")


if __name__ == "__main__":
    main()
