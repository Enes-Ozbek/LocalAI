"""Makes training conversations for the leftover cleanup: the model gets the facts of a PC's app folders
(schema.cleanup_result) and answers with propose_cleanup.

    python dataset/make_leftovers.py                 # 300 made-up PCs -> dataset/leftovers.jsonl
    python dataset/make_leftovers.py --pcs 500

No files and no teacher model are needed. Each made-up PC has a random set of real-world apps, some installed and
some uninstalled, and the folders those apps keep in AppData and the home folder. The facts are built with the
app's own code (leftovers.closest, leftovers.own_work), so training and use see the same text, and the right answer
follows from facts the model can see:
  leftover  the app is not installed and nothing in the facts says otherwise
  cache     the app is installed, and at least two thirds of the folder's files are cache or logs
  ask       game saves or other own work, an installed app's folder whose owner the facts don't show, or an
            uninstalled app's folder that an installed program's name comes close to
  (left out) an installed app's folder whose owner the facts show: a close name, a running program, or the app
             installed inside it
A leftover changed in the last 14 days is still called a leftover: the app's rules hold it back, not the model.
About a third of the requests name one app ('clean up after Ollama'): then only that app's folders are shown.
split.py puts each PC in one split.
"""

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant import leftovers  # noqa: E402
from assistant.leftovers import Program  # noqa: E402
from assistant.schema import LEFTOVER_PROMPT, cleanup_result  # noqa: E402

HERE = Path(__file__).resolve().parent
L, R, LL = "AppData/Local", "AppData/Roaming", "AppData/LocalLow"

# app: (publisher, kind, [(folder, what is in it)], installed inside one of its folders?)
# what is in it: settings, cache, logs, data (downloads, models, databases), saves, docs, program
# kind: app, game, dev (a tool whose folder is a cache), driver (hardware software)
APPS = {
    "Docker Desktop": ("Docker Inc.", "dev", [(f"{L}/Docker", "data"), (f"{R}/Docker", "settings"), (".docker", "settings")], False),
    "Ollama": ("Ollama", "app", [(f"{L}/Ollama", "logs"), (f"{R}/ollama app.exe", "settings"), (".ollama", "data")], False),
    "LM Studio": ("Element Labs", "app", [(f"{R}/LM Studio", "settings"), (".lmstudio", "data")], False),
    "Discord": ("Discord Inc.", "app", [(f"{L}/Discord", "program"), (f"{R}/discord", "cache")], True),
    "Spotify": ("Spotify AB", "app", [(f"{R}/Spotify", "program"), (f"{L}/Spotify", "cache")], True),
    "Slack": ("Slack Technologies", "app", [(f"{L}/slack", "program"), (f"{R}/Slack", "cache")], True),
    "Zoom": ("Zoom Video Communications", "app", [(f"{R}/Zoom", "program"), (f"{L}/Zoom", "logs")], True),
    "Telegram Desktop": ("Telegram FZ-LLC", "app", [(f"{R}/Telegram Desktop", "program")], True),
    "WhatsApp": ("WhatsApp", "app", [(f"{L}/WhatsApp", "program")], True),
    "Notion": ("Notion Labs", "app", [(f"{R}/Notion", "cache"), (f"{L}/Programs/Notion", "program")], True),
    "Figma": ("Figma, Inc.", "app", [(f"{L}/Figma", "program"), (f"{R}/Figma", "cache")], True),
    "Postman": ("Postman", "dev", [(f"{L}/Postman", "program"), (f"{R}/Postman", "settings")], True),
    "GitHub Desktop": ("GitHub, Inc.", "dev", [(f"{L}/GitHubDesktop", "program"), (f"{R}/GitHub Desktop", "settings")], True),
    "Steam": ("Valve Corporation", "app", [(f"{L}/Steam", "cache")], False),
    "Epic Games Launcher": ("Epic Games, Inc.", "app", [(f"{L}/EpicGamesLauncher", "logs"), (f"{R}/Epic", "settings")], False),
    "Origin": ("Electronic Arts", "app", [(f"{L}/Origin", "logs"), (f"{R}/Origin", "settings")], False),
    "EA app": ("Electronic Arts", "app", [(f"{L}/Electronic Arts", "logs"), (f"{L}/EADesktop", "settings")], False),
    "Ubisoft Connect": ("Ubisoft", "app", [(f"{L}/Ubisoft Game Launcher", "cache")], False),
    "Battle.net": ("Blizzard Entertainment", "app", [(f"{R}/Battle.net", "settings"), (f"{L}/Battle.net", "cache")], False),
    "Riot Client": ("Riot Games, Inc", "app", [(f"{L}/Riot Games", "logs"), (f"{R}/Riot Client", "settings")], False),
    "Overwolf": ("Overwolf Ltd.", "app", [(f"{L}/Overwolf", "logs"), (f"{R}/ow-electron", "cache")], False),
    "OBS Studio": ("OBS Project", "app", [(f"{R}/obs-studio", "settings")], False),
    "VLC media player": ("VideoLAN", "app", [(f"{R}/vlc", "settings")], False),
    "Audacity": ("Audacity Team", "app", [(f"{R}/audacity", "settings")], False),
    "GIMP": ("The GIMP Team", "app", [(f"{R}/GIMP", "settings")], False),
    "Blender": ("Blender Foundation", "app", [(f"{R}/Blender Foundation", "settings")], False),
    "Unity Hub": ("Unity Technologies", "dev", [(f"{R}/UnityHub", "settings"), (f"{L}/Unity", "cache")], False),
    "Godot Engine": ("Godot Engine", "dev", [(f"{R}/Godot", "settings")], False),
    "Android Studio": ("Google LLC", "dev", [(f"{L}/Android", "data"), (".android", "settings"), (".gradle", "cache")], False),
    "Arduino IDE": ("Arduino SA", "dev", [(f"{L}/Arduino15", "data"), (f"{R}/arduino-ide", "settings"), (".arduinoIDE", "settings")], False),
    "Cisco Packet Tracer": ("Cisco Systems", "app", [(f"{L}/Cisco Packet Tracer", "logs"), (f"{R}/Cisco Packet Tracer", "settings")], False),
    "MySQL Workbench": ("Oracle Corporation", "dev", [(f"{R}/MySQL", "settings")], False),
    "Wireshark": ("The Wireshark developer community", "app", [(f"{R}/Wireshark", "settings")], False),
    "Razer Synapse": ("Razer Inc.", "driver", [(f"{L}/Razer", "logs")], False),
    "Logitech G HUB": ("Logitech", "driver", [(f"{L}/LGHUB", "logs")], False),
    "Corsair iCUE": ("Corsair", "driver", [(f"{L}/Corsair", "logs"), (f"{R}/Corsair", "settings")], False),
    "Brave": ("Brave Software Inc", "app", [(f"{L}/BraveSoftware", "cache")], False),
    "Opera": ("Opera Software", "app", [(f"{R}/Opera Software", "cache")], False),
    "4K Video Downloader": ("Open Media LLC", "app", [(f"{L}/4kdownload.com", "settings")], False),
    "qBittorrent": ("The qBittorrent project", "app", [(f"{R}/qBittorrent", "settings"), (f"{L}/qBittorrent", "logs")], False),
    "WinRAR": ("win.rar GmbH", "app", [(f"{R}/WinRAR", "settings")], False),
    "Dropbox": ("Dropbox, Inc.", "app", [(f"{L}/Dropbox", "logs")], False),
    "Anaconda": ("Anaconda, Inc.", "dev", [(".conda", "settings"), (f"{L}/conda", "cache")], False),
    "Node.js": ("Node.js Foundation", "dev", [(f"{L}/npm-cache", "cache"), (f"{R}/npm", "program")], False),
    "Python": ("Python Software Foundation", "dev", [(f"{L}/pip", "cache")], False),
    "Playwright": ("Microsoft", "dev", [(f"{L}/ms-playwright", "data")], False),
    "Minecraft": ("Mojang", "game", [(".minecraft", "saves")], False),
    "Elden Ring": ("Bandai Namco", "game", [(f"{R}/EldenRing", "saves")], False),
    "Hollow Knight": ("Team Cherry", "game", [(f"{LL}/Team Cherry", "saves")], False),
    "Stardew Valley": ("ConcernedApe", "game", [(f"{R}/StardewValley", "saves")], False),
    "Factorio": ("Wube Software", "game", [(f"{R}/Factorio", "saves")], False),
    "Fallout 76": ("Bethesda Softworks", "game", [(f"{L}/Fallout76", "settings")], False),
    "Baldur's Gate 3": ("Larian Studios", "game", [(f"{L}/Larian Studios", "saves")], False),
    "Lethal Company": ("Zeekerss", "game", [(f"{LL}/ZeekerssRBLX", "saves")], False),
    "Visual Studio Code": ("Microsoft Corporation", "dev", [(f"{R}/Code", "cache"), (".vscode", "data")], False),
    "PyCharm": ("JetBrains s.r.o.", "dev", [(f"{L}/JetBrains", "cache"), (f"{R}/JetBrains", "settings")], False),
    "QProctor": ("QProctor", "app", [(f"{R}/QProctor", "logs")], False),
}
INSIDE = {"settings": ["User", "config", "Preferences", "settings"], "cache": ["Cache", "Code Cache", "GPUCache", "cache"],
          "logs": ["logs", "Logs", "log"], "data": ["models", "data", "blobs", "downloads"],
          "saves": ["saves", "Saves", "SaveGames"], "docs": ["projects", "Documents"], "program": ["app-1.0", "bin"]}
GENERIC_REQUESTS = ["find leftovers of apps I uninstalled", "find leftover files of uninstalled apps",
                    "clean up leftovers of programs I removed", "what did uninstalled apps leave behind?",
                    "clean appdata", "find leftover files on my pc", "remove leftovers of apps I don't have anymore",
                    "clean up files left by uninstalled programs", "show me leftovers from old apps",
                    "my disk is full, find leftovers of apps I uninstalled"]
NAMED_REQUESTS = ["clean up after {a}", "remove {a} leftovers", "I uninstalled {a}, clean up its files",
                  "what did {a} leave behind?", "find leftover files of {a}", "delete {a} leftovers",
                  "clean up after {a}, I uninstalled it"]


def folder_facts(r: random.Random, folder: str, what: str, installed: bool, running: bool, recent: bool) -> tuple:
    """(facts as leftovers.sheet writes them, code's own counts). Made up, but in the same shape and ranges."""
    n = r.randint(1, 400) if what not in ("settings",) else r.randint(1, 30)
    kinds = {"programs": 0, "cache_logs": 0, "saves": 0, "documents": 0, "photos_videos": 0, "settings_data": 0}
    main = {"settings": "settings_data", "cache": "cache_logs", "logs": "cache_logs", "data": "cache_logs",
            "saves": "saves", "docs": "documents", "program": "programs"}[what]
    kinds[main] = max(1, int(n * r.uniform(0.7, 1.0)))
    rest = max(0, n - kinds[main])
    if what in ("cache", "logs") and rest:
        kinds["settings_data"] += min(rest, max(0, kinds[main] // 3 - 1))  # stays a cache: 2/3 or more
    elif rest:
        kinds["settings_data" if main != "settings_data" else "cache_logs"] += rest
    if what == "program":
        kinds["cache_logs"] += r.randint(0, 50)
        kinds["settings_data"] += r.randint(1, 20)
    if what == "saves":
        kinds["settings_data"] += r.randint(1, 5)
    if what == "docs":
        kinds["documents"] = max(3, kinds["documents"])
    mb = {"settings": r.uniform(0.002, 3), "cache": r.uniform(1, 3000), "logs": r.uniform(0.01, 200),
          "data": r.uniform(50, 30000), "saves": r.uniform(0.05, 800), "docs": r.uniform(1, 500),
          "program": r.uniform(80, 900)}[what]
    if installed:
        days = r.choice([0, 0, 1, 2, 3, 5, 9, 20, 45])
    else:
        days = r.randint(0, 13) if recent else r.choice([r.randint(14, 90), r.randint(90, 400), r.randint(400, 1500)])
    files = sum(kinds.values())
    names = r.sample(INSIDE[what], k=min(len(INSIDE[what]), r.randint(1, 3)))
    w = {"bytes": int(mb * 1048576), "files": files, "more": False, "days": days,
         "kinds": {k: v for k, v in kinds.items() if v}, "git": what == "docs" and r.random() < 0.5,
         "biggest": [{"name": nm, "mb": round(mb * share, 1)} for nm, share in zip(names, (0.8, 0.15, 0.05))]}
    sheet = {"folder": folder, "size_mb": leftovers._mb(w["bytes"]), "files": files, "last_change_days": days,
             "kinds": w["kinds"]}
    if w["biggest"]:
        sheet["biggest_inside"] = [f"{b['name']} {b['mb']} MB" for b in w["biggest"]]
    if w["git"]:
        sheet["has_git_project"] = True
    return sheet, w


def answer(app: str, kind: str, what: str, sheet: dict, w: dict, installed: bool) -> dict:
    """The right entry for one folder, or None when it is kept (left out)."""
    owner_shown = sheet["used_by_running_program"] or any(m["score"] >= leftovers.OWNED for m in sheet["closest_installed"])
    cachey = w["kinds"].get("cache_logs", 0) >= 2 * w["files"] / 3
    if installed:
        if not owner_shown:
            return {"verdict": "ask", "reason": f"looks like {app}'s folder, but no installed program clearly owns it"}
        if cachey and what in ("cache", "logs", "data") and not sheet["used_by_running_program"]:
            return {"verdict": "cache", "reason": f"cache and logs of {app}, which is installed; it makes them again"}
        return None
    near = next((m for m in sheet["closest_installed"] if m["score"] >= leftovers.OWNED), None)
    if near:
        return {"verdict": "ask", "reason": f"{app} is not installed, but {near['program']} is and may use this folder"}
    if leftovers.own_work(w):
        why = "game saves" if w["kinds"].get("saves") else "a project" if w["git"] else "documents or photos"
        gone = "the game is" if kind == "game" else f"{app} is"
        return {"verdict": "ask", "reason": f"{why} of {app}; {gone} not installed, but they may matter to you"}
    held = {"settings": "settings are", "cache": "cache is", "logs": "logs are", "data": "downloaded data is",
            "program": "program files are", "saves": "files are", "docs": "files are"}[what]
    return {"verdict": "leftover", "reason": f"{app} is not installed; only its {held} left"}


def make_pc(r: random.Random, index: int) -> dict:
    names = r.sample(sorted(APPS), k=r.randint(10, 20))
    installed = {a: r.random() < 0.5 for a in names}
    programs, running, folders = [], [], []
    for a in names:
        publisher, kind, places, inside = APPS[a]
        if installed[a]:
            where = places[0][0] if inside else f"C:/Program Files/{a}"
            programs.append(Program(a if r.random() < 0.8 else f"{a} {r.randint(1, 30)}.{r.randint(0, 9)}", publisher, where))
            if inside and r.random() < 0.6:
                running.append(f"{where}/{a.split()[0]}.exe")
    recent = {a: not installed[a] and r.random() < 0.15 for a in names}
    for a in names:
        for folder, what in APPS[a][2]:
            if r.random() < 0.85:
                folders.append((a, folder, what))
    r.shuffle(folders)
    folders = folders[:leftovers.PART]
    named = r.random() < 0.3 and any(not installed[a] for a, _, _ in folders)
    if named:
        app = r.choice(sorted({a for a, _, _ in folders if not installed[a]}))
        folders = [f for f in folders if f[0] == app]
        request = r.choice(NAMED_REQUESTS).format(a=r.choice([app, app.lower(), app.split()[0]]))
        only = leftovers.named_app(request) or app
    else:
        request, only = r.choice(GENERIC_REQUESTS), ""
    sheets, items = [], []
    for a, folder, what in folders:
        sheet, w = folder_facts(r, folder, what, installed[a], False, recent[a])
        sheet["closest_installed"] = [{"program": p.name, "score": s}
                                      for p, s in leftovers.closest(Path(folder), programs)]
        sheet["used_by_running_program"] = leftovers.in_use(Path(folder), running)
        sheets.append(sheet)
        entry = answer(a, APPS[a][1], what, sheet, w, installed[a])
        if entry:
            items.append({"folder": folder, "verdict": entry["verdict"], "app": a, "reason": entry["reason"]})
    messages = [{"role": "system", "content": LEFTOVER_PROMPT}, {"role": "user", "content": request},
                {"role": "assistant", "content": "",
                 "tool_calls": [{"function": {"name": "inspect_app_folders", "arguments": {"app": only}}}]},
                {"role": "tool", "content": cleanup_result(sheets, only)},
                {"role": "assistant", "content": "",
                 "tool_calls": [{"function": {"name": "propose_cleanup", "arguments": {"items": items}}}]}]
    return {"mode": "cleanup", "meta": {"scenario_id": f"pc-{index:04}", "task_id": f"pc-{index:04}-c",
                                        "request_type": "cleanup_named" if named else "cleanup", "folders": len(sheets),
                                        "verdicts": {v: sum(i["verdict"] == v for i in items)
                                                     for v in leftovers.VERDICTS}},
            "messages": messages}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pcs", type=int, default=300)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--out", default=str(HERE / "leftovers.jsonl"))
    args = ap.parse_args()
    r = random.Random(args.seed)
    folders = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for i in range(args.pcs):
            rec = make_pc(r, i)
            folders += rec["meta"]["folders"]
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"Wrote {args.pcs} cleanup conversations ({folders} folder decisions) to {args.out}")


if __name__ == "__main__":
    main()
