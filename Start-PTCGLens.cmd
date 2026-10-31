@echo off
setlocal
set "PTCGLENS_PROJECT=%~dp0"
if not exist "%PTCGLENS_PROJECT%.venv\Scripts\pythonw.exe" (
    echo Python environment missing. Run uv sync in this project first.
    pause
    exit /b 1
)
start "" "%PTCGLENS_PROJECT%.venv\Scripts\pythonw.exe" "%PTCGLENS_PROJECT%ptcglens.py"
