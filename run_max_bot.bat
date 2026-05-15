@echo off
cd /d "%~dp0"
if not exist "school-bot-max\venv" (
    py -3 -m venv school-bot-max\venv
    school-bot-max\venv\Scripts\python.exe -m pip install -U pip
    school-bot-max\venv\Scripts\python.exe -m pip install -r requirements.txt
)
school-bot-max\venv\Scripts\python.exe school-bot-max\bot.py
pause
