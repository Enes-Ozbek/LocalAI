@echo off
rem Double-click to open the Folder Assistant window in your browser.
rem Keep this window open while you use the app. Press Ctrl+C here or close this window to quit.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The .venv folder is missing. Set it up first: see "Setup (Windows)" in README.md.
    pause
    exit /b 1
)
set "ANSWER=y"
set /p ANSWER="Use the test sandbox instead of your real folders? [Y/n] "
if /i "%ANSWER:~0,1%"=="n" (
    set "BASE=%USERPROFILE%"
) else (
    if not exist sandbox ".venv\Scripts\python.exe" make_sandbox.py
    set "BASE=sandbox"
)
".venv\Scripts\python.exe" app.py --base "%BASE%"
pause
