"""What the model sees: the system prompt and the four tools.

inspect_folder    read-only; returns the folder's subfolders and one fact sheet per file
propose_changes   the model's plan (move / rename / delete + reason per file);
                  code checks it, shows a preview, and runs it only after the user confirms
ask_clarification, refuse

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
                "to_folder": {"type": "string", "description": "Subfolder inside this folder, for move, e.g. 'Bills'"},
                "new_name": {"type": "string", "description": "New file name, for rename or move"},
                "reason": {"type": "string", "description": "Short reason, always give one"}},
                # reason is not "required": llama.cpp writes required fields first, and after the
                # reason the 4B often ended the entry without to_folder. The app fills a missing reason.
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
