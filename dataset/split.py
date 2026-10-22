"""Splits the reviewed records into train / validation / test, by folder scenario.

    python dataset/split.py

All requests about the same folder land in the same split, so the test set only has
folders the model has never seen. The hand-written seed examples always go to training.
Writes dataset/train.jsonl, dataset/val.jsonl, dataset/test.jsonl.
"""

import json
import random
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(seed=7, val=0.1, test=0.1):
    records = [json.loads(line) for line in open(HERE / "reviewed.jsonl", encoding="utf-8") if line.strip()]
    scenarios = sorted({r["meta"]["scenario_id"] for r in records})
    random.Random(seed).shuffle(scenarios)
    n_test, n_val = round(len(scenarios) * test), round(len(scenarios) * val)
    split_of = {s: "test" for s in scenarios[:n_test]}
    split_of.update({s: "val" for s in scenarios[n_test:n_test + n_val]})

    out = {"train": [], "val": [], "test": []}
    for r in records:
        out[split_of.get(r["meta"]["scenario_id"], "train")].append(r)
    seeds = HERE / "seed_examples.jsonl"
    if seeds.exists():
        out["train"] += [json.loads(line) for line in open(seeds, encoding="utf-8") if line.strip()]

    for name, rows in out.items():
        with open(HERE / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        kinds = Counter(r.get("meta", {}).get("request_type", "seed") for r in rows)
        print(f"{name:5}: {len(rows):5} records  {dict(kinds)}")


if __name__ == "__main__":
    main()
