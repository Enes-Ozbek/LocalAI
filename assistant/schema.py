"""What the model sees: the system prompt and the four tools.

inspect_folder    read-only; returns the folder's subfolders and one fact sheet per file
propose_changes   the model's plan (move / rename / delete + reason per file);
                  code checks it, shows a preview, and runs it only after the user confirms
ask_clarification, refuse

and for questions about files, a second prompt with three read-only tools (answers.py runs them):
list_files, folder_summary, search_files

Training data and the app both import from here, so they can never disagree.
"""

import json

SYSTEM_PROMPT = (
    "You organize folders on the user's Windows PC. "
    "If the user names no folder, call ask_clarification; never pick a folder yourself. "
    "Otherwise call inspect_folder to see the files. Then explain briefly what the files are. "
    "If the user asked to organize, sort or clean, call propose_changes in the same reply, with one entry "
    "per file you change. Do not ask whether to go ahead: the app shows the user a preview and asks first. "
    "If the user only wants to know what is there, propose nothing. "
    "When organizing, move every file that clearly belongs with others into a subfolder for that group. "
    "Follow the user's limits (for example 'don't delete anything'). Change each file at most once. "
    "When unsure about a file, leave it. "
    "System folders or anything that is not about organizing files: refuse."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "inspect_folder",
        "description": "List the files in a folder with facts about each (type, size, dates, duplicates, first text).",
        "parameters": {"type": "object", "properties": {
            "folder": {"type": "string", "description": "e.g. 'Downloads' or 'Downloads/New folder1'"}},
            "required": ["folder"]}}},
    {"type": "function", "function": {
        "name": "propose_changes",
        "description": "Propose changes for the user to approve.",
        "parameters": {"type": "object", "properties": {
            "folder": {"type": "string"},
            "changes": {"type": "array", "items": {"type": "object", "properties": {
                "file": {"type": "string"},
                "action": {"type": "string", "enum": ["move", "rename", "delete"]},
                "reason": {"type": "string", "description": "Short reason, always give one"},
                "to_folder": {"type": "string", "description": "Subfolder inside this folder, for move, e.g. 'Bills'"},
                "new_name": {"type": "string", "description": "New file name, for rename or move"}},
                # llama.cpp only lets the model write these keys in this order. The fine-tuned model writes
                # the reason before the target (as in its training data); with the reason last it ended
                # 55 moves without to_folder. reason is not "required" so a model can also go straight to
                # the target; the app fills a missing reason.
                "required": ["file", "action"]}}},
            "required": ["folder", "changes"]}}},
    {"type": "function", "function": {
        "name": "ask_clarification",
        "description": "Ask the user when the request is unclear.",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}},
                       "required": ["question"]}}},
    {"type": "function", "function": {
        "name": "refuse",
        "description": "Refuse system folders and requests that are not about organizing files.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}},
                       "required": ["reason"]}}},
]

TOOL_NAMES = {t["function"]["name"] for t in TOOLS}

# Added to every inspect_folder result: small models follow a reminder next to the data better
# than one in the system prompt, and without it the 4B asks "shall I?" instead of proposing.
NEXT_STEP = ("Now explain briefly what these files are. If the user asked to organize, sort or clean, "
             "call propose_changes in this same reply, with a reason for every file. "
             "Do not ask first: the app shows the user a preview and asks before changing anything.")


def tool_result(facts: dict) -> str:
    """The inspect_folder result exactly as the model sees it, in the app and in the training data."""
    return json.dumps({**facts, "next_step": NEXT_STEP}, ensure_ascii=False)


QUESTION_PROMPT = (
    "You answer questions about the files on the user's Windows PC. Never guess a number or a file name: "
    "call a tool first, then answer from its result. "
    "list_files counts, filters and sorts files, for example 'how many pdfs', 'the biggest files' or "
    "'photos from 2024'. folder_summary tells how many files of each kind a folder has, how much space they take, "
    "and its subfolders. search_files finds files by name, words inside them or meaning. "
    "If neither the user nor the conversation names a folder, leave folder empty: the tools then cover the "
    "user's main folders. Answer in one or two short sentences, with the numbers and the folder. "
    "You can't change files here: if the user wants changes, tell them to ask for them, for example "
    "'delete the .tmp files in Downloads'."
)


def question_prompt(last_folder: str = None) -> str:
    """The question prompt, plus the folder the conversation was last about ('Downloads')."""
    return QUESTION_PROMPT + (f" The last answer was about the folder {last_folder}." if last_folder else "")


# (the tools for questions; answers.run_tool runs them)
READ_TOOLS = [
    {"type": "function", "function": {
        "name": "list_files",
        "description": "Count, filter and sort files. Gives the exact number and total size of the files that match, "
                       "and shows up to `limit` of them.",
        "parameters": {"type": "object", "properties": {
            "folder": {"type": "string", "description": "e.g. 'Downloads' or 'Downloads/New folder1'. "
                                                        "Empty: the user's main folders."},
            "kind": {"type": "string", "description": "photos, videos, music, documents, spreadsheets, presentations, "
                                                      "archives, installers, screenshots, temporary, duplicates or empty"},
            "extension": {"type": "string", "description": "e.g. '.pdf'"},
            "name_contains": {"type": "string"},
            "newer_than_days": {"type": "integer"},
            "older_than_days": {"type": "integer"},
            "min_size_mb": {"type": "number"},
            "max_size_mb": {"type": "number"},
            "sort_by": {"type": "string", "enum": ["size", "date", "name"]},
            "order": {"type": "string", "enum": ["desc", "asc"], "description": "desc: biggest or newest first"},
            "limit": {"type": "integer", "description": "how many files to show, default 10"},
            "include_subfolders": {"type": "boolean"}}}}},
    {"type": "function", "function": {
        "name": "folder_summary",
        "description": "How many files a folder has, of which kinds, how much space they take, and its subfolders. "
                       "Without a folder: the same for each of the user's main folders.",
        "parameters": {"type": "object", "properties": {"folder": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "search_files",
        "description": "Find files by name, words inside them or meaning ('my CV', 'electricity bill', "
                       "'pdfs from last month').",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "folder": {"type": "string", "description": "Empty: all the user's folders."}},
            "required": ["query"]}}},
]

QUESTION_TOOL_NAMES = {t["function"]["name"] for t in READ_TOOLS}


# (leftovers of uninstalled apps: assistant/leftovers.py gathers one fact sheet per app folder, the model sorts them)
LEFTOVER_PROMPT = (
    "You find folders that apps the user uninstalled left behind on their Windows PC. "
    "Each folder comes with facts: its size, how many files, how many days since anything in it changed, "
    "what kinds of files it holds, its biggest subfolders, the installed programs whose names come closest "
    "(score 1 means the same name) and whether a running program uses it. "
    "Call propose_cleanup with one entry for each folder that is one of these: "
    "leftover: no installed program owns it and it holds only the app's own data (settings, cache, logs, downloads); "
    "cache: it belongs to an installed program but holds only cache, temporary files or logs that the program "
    "makes again; "
    "ask: it may hold the user's own work (game saves, documents, projects, photos), or you are not sure. "
    "Leave out folders that belong to an installed program or to Windows: they are kept. "
    "Give the app's name and a short reason for every entry. Do not ask whether to go ahead: "
    "the app shows the user a preview and asks before anything changes."
)

CLEANUP_TOOLS = [
    {"type": "function", "function": {
        "name": "inspect_app_folders",
        "description": "Facts about the app folders in the user's AppData and home folder, a part at a time.",
        "parameters": {"type": "object", "properties": {
            "app": {"type": "string", "description": "Only folders of this app, e.g. 'Ollama'. Empty: all."}}}}},
    {"type": "function", "function": {
        "name": "propose_cleanup",
        "description": "The folders to clean up, for the user to approve. Folders left out are kept.",
        "parameters": {"type": "object", "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "folder": {"type": "string", "description": "exactly as in the facts, e.g. 'AppData/Local/Origin'"},
                "verdict": {"type": "string", "enum": ["leftover", "cache", "ask"]},
                "app": {"type": "string", "description": "the app the folder belongs to"},
                "reason": {"type": "string", "description": "Short reason, always give one"}},
                "required": ["folder", "verdict"]}}},
            "required": ["items"]}}},
]

CLEANUP_TOOL_NAMES = {t["function"]["name"] for t in CLEANUP_TOOLS}

# Added to every inspect_app_folders result, as NEXT_STEP is to inspect_folder's
CLEANUP_NEXT = ("Now call propose_cleanup with an entry for every folder that is a leftover, a cache or one to ask "
                "about. Leave out the folders to keep. Do not ask first: the app shows the user a preview.")


def cleanup_result(folders: list, app: str = "") -> str:
    """The inspect_app_folders result exactly as the model sees it, in the app and in the training data."""
    out = {"folders": folders, "next_step": CLEANUP_NEXT}
    if app:
        out = {"only_app": app, **out}
    return json.dumps(out, ensure_ascii=False)
