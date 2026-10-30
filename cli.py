"""Chat with the assistant in the terminal.

    python cli.py                    # works on your real folders (asks before every change)
    python cli.py --base sandbox     # 'Downloads' means sandbox/Downloads: safe for testing

Type a request, read the explanation and the preview, then answer y to apply.
Type 'undo' to reverse the last applied changes, 'quit' to exit.
"""

import argparse
from pathlib import Path

from assistant import agent, executor, llm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(Path.home()), help="where folder names are looked up")
    ap.add_argument("--model", default=llm.MODEL)
    args = ap.parse_args()
    base = Path(args.base).resolve()
    print(f"Model: {llm.label(args.model)}   Folders under: {base}\nType a request, 'undo' or 'quit'.")
    session = agent.Session(base, args.model)

    while True:
        try:
            text = input("\nYou: ").strip()
        except EOFError:
            break
        if text.lower() in ("", "quit", "exit"):
            break
        if text.lower() == "undo":
            print(executor.undo_last())
            continue

        r = session.run(text)
        print(f"\nAssistant ({' + '.join(f'{s}s' for s in r.seconds)}):")
        if r.text:
            print(r.text)
        for m in r.matches:
            print(f"  {m['path']}  ({m['modified']}, {m['size_kb']} KB)  {m['why']}")
        if r.kind != "proposal":
            continue
        for b in r.blocked:
            print(f"  BLOCKED {b['file']}: {b['blocked']}")
        if not r.steps:
            print("No changes to make.")
            continue
        print("\nPlanned changes:\n" + executor.preview(r.steps, r.folder))
        if input("\nApply these changes? (y/n) ").strip().lower() == "y":
            log = executor.apply(r.steps)
            print(f"Done. Type 'undo' to reverse. (log: {log})")


if __name__ == "__main__":
    main()
