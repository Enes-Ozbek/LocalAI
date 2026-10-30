# The app window: how it looks and why

The window works like a folder. For every request that changes files it shows the folder twice: **Now**, as it is,
and **After**, as it will be once the changes are made. People already know how to read a folder full of icons, so
they can see what the assistant is about to do before anything moves. Everything is in `assistant/app.html`; the
data comes from `app.py`.

## The screen

- **Top bar:** "Folder Assistant", the request box (Enter sends; clicking it selects the request that is there, so
  typing replaces it), "Test sandbox" when the app runs with `--base sandbox`, and "Undo last run", always.
- **Heading:** "Here's New folder1 after tidying", then one line: "9 items become 3 folders and 1 file. Nothing has
  changed yet."
- **Now window (left, 340px):** the files that change first, each with a checkbox, a small icon, its name and where it
  goes ("→ Schoolwork", "→ Bills, renamed", "→ Recycle Bin", "→ Documents" when it leaves the folder). Unticked rows
  say "stays". Below a "Not changed" divider: everything else in the folder; a file the safety rules kept says "kept"
  and has no checkbox. A checkbox in the bar ticks or unticks all.
- **After window (right):** big icons. New folders first, marked "New", then existing folders, then files by name.
  Double-click a folder (or press Enter) to look inside; the path bar shows where you are and ‹ goes back. Click
  anything and the status bar says why: "Turkcell_2026-09.pdf · was scan_0031.pdf · from Downloads/New folder1 ·
  Turkcell phone bill". Under the icons: one dashed line for what goes to the Recycle Bin and one for files that move
  to other folders. The After window is worked out in the page from the ticks, so it changes the moment a box is
  unticked; the model is never asked again.
- **Action bar:** what will happen ("6 files into 3 new folders · 1 renamed · 2 to the Recycle Bin. Nothing is written
  until you apply, and you can undo."), Cancel, and "Apply 8 changes" (with nothing ticked: "Nothing selected",
  disabled). It stays at the bottom of the window while you scroll.
- **Under the windows:** the changes the safety rules blocked, and what the model said ("What I found", folded when
  long).
- **Status line:** Ready / Working… / Applying… / Undoing…, the folder names are looked up under, "Deletes go to the
  Recycle Bin", and the model and backend.

Other screens: the start (examples from `app.py`), working (with "part 2 of 4" for big folders), done (the folder
as it is now, with "Undo this run"), a question answered, a folder explained (its icons, nothing proposed), "Which
folder do you mean?", "I won't do that", search results in a details view (tick, then delete or move into one folder,
which shows the destination folder after the move, or the Recycle Bin), and the errors.

## Rules

- Write nothing before Apply. Apply sends only the row numbers that are ticked; the plan itself stays on the server.
- Every change shows up twice: as a row in Now, and as its result in After.
- Deletes go to the Recycle Bin, and say so in words, in `--delete`, every time.
- File names exactly as on disk, and only ever through `textContent`, never as HTML.
- The page loads nothing from outside the PC: no fonts, scripts or images from the internet.
- Short, plain writing: sentence case, "you", no emoji, no exclamation marks. Say what happens on disk.

## Colours

CSS variables at the top of `app.html`, with a light and a dark set (the page follows the system). Text holds 4.5:1
on its background in both, borders and icon outlines 3:1.

| Variable | Light | Dark | Used for |
| --- | --- | --- | --- |
| `--bg` | `#f6f6f4` | `#121313` | Page background |
| `--surface` | `#ffffff` | `#1c1d1f` | Windows, top bar, status line, buttons |
| `--text` / `--text-muted` | `#1a1a1c` / `#5f6066` | `#ececed` / `#a3a4aa` | Names and headings / destinations, counts, the status bar |
| `--line` / `--line-strong` | `#e5e5e2` / `#86878d` | `#2d2e31` / `#6f7076` | Window edges / checkbox, input and button borders |
| `--accent`, `--on-accent` | `#1d5bbf`, `#ffffff` | `#8db5ff`, `#0f1726` | Apply, ticks, the selected tile, "New", the focus ring |
| `--accent-tint` | `#eaf1fc` | `#1b2638` | Hover, the selected tile, the "New" label, the done banner |
| `--delete` | `#b3361a` | `#ff8c6b` | The Recycle Bin, only |
| `--caution` | `#8a5a00` | `#e6b450` | Blocked changes, "kept", the sandbox label |
| `--folder`, `--folder-back`, `--folder-edge` | `#f3c457`, `#dca43a`, `#9a6a10` | `#e2b24a`, `#b8892c`, `#e2b24a` | The folder icon |
| `--file-paper`, `--file-edge` | `#ffffff`, `#86878d` | `#2b2c30`, `#8a8b91` | The file icon |
| `--type-pdf`, `-image`, `-doc`, `-app`, `-text`; `--on-type` | `#c0392b`, `#1f7a52`, `#2f62c4`, `#55595f`, `#7c6418`; `#ffffff` | `#ff8a80`, `#6fd39a`, `#8db5ff`, `#b9bcc6`, `#d9bf7a`; `#121313` | The label band on a file icon, by extension (photos, video and sound are `image`; Office files `doc`; txt, md, csv, log, json `text`; everything else grey) |

## Type, shape, icons

- **Font:** Figtree (400 to 700), from `assistant/fonts`, served by `app.py` at `/fonts/…`. Headings 22px/600, body
  15px, names under icons 13px/500, everything small 13px in `--text-muted`. No monospace.
- **Shape:** windows 12px corners, tiles and buttons 8px, checkboxes 5px, labels fully round. No shadows or gradients.
- **Icons:** drawn in the page as SVG (no image files): a folder, a file page with a coloured band carrying its
  extension (56px in After; 22px in the lists, without the letters), the Recycle Bin, and four marks for notices.
- **States:** hover and selected use `--accent-tint` (selected adds a 1px `--accent` border); unticked names are struck
  through in `--text-muted`; disabled buttons have a dashed border; focus is a 2px `--accent` ring. The only motion is
  the spinner while the model works, and it stops for people who ask for reduced motion.
- **Narrow windows:** under 880px the two windows stack; under 640px the request box gets its own line.

## What app.py sends for this

`/api/run` and `/api/act` answers carry `view` (the folder now: `name`, `path`, `exists`, `picked` for search
results, `entries` of `{name, dir, items}` or `{name, dir, size_kb, modified}`, at most 1000, and `more`) and, per
step, `name`, `from`, `here`, `bin`, and for moves `to` (the subfolders it goes into, `[]` for the folder itself,
`null` when it leaves it), `to_new`, `new_name` and `out`. The page never parses paths. `tests/test_app.py` covers it.
