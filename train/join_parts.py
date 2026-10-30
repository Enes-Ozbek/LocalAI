"""Joins the .partNN files that colab/finetune_colab.ipynb downloads back into one .gguf file,
and checks it against the SHA-256 that Colab wrote next to them.

    python train/join_parts.py models

It never overwrites an existing .gguf: rename the old model first (every run's file has the same name).
"""

import glob
import hashlib
import os
import sys

folder = sys.argv[1] if len(sys.argv) > 1 else "models"
sha_files = glob.glob(os.path.join(folder, "*.sha256.txt"))
if not sha_files:
    sys.exit(f"No .sha256.txt file in {folder}. Download it from Colab together with the parts.")
for sha_file in sha_files:
    expected, name = open(sha_file, encoding="utf-8").read().split()[:2]
    parts = sorted(glob.glob(os.path.join(folder, name + ".part*")))
    target = os.path.join(folder, name)
    if not parts:
        print(f"{name}: no parts next to {os.path.basename(sha_file)}, skipped")
        continue
    if os.path.exists(target):
        sys.exit(f"{target} already exists. Rename the old model first, then run this again.")
    sha = hashlib.sha256()
    with open(target, "wb") as out:
        for part in parts:
            with open(part, "rb") as f:
                while chunk := f.read(16 * 2**20):
                    sha.update(chunk)
                    out.write(chunk)
    if sha.hexdigest() != expected:
        os.remove(target)
        sys.exit(f"{name}: joined {len(parts)} parts, but the SHA-256 does not match. A part is missing or broken.")
    for part in parts:
        os.remove(part)
    print(f"{target}: {len(parts)} parts joined, {os.path.getsize(target) / 2**30:.2f} GB, SHA-256 matches")
