@echo off
cd /d "%~dp0"
python update_gui.py
if errorlevel 1 pause
