@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
python serve_ui.py
pause
