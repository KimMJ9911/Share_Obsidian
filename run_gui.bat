@echo off
rem Runs the GUI directly from source without building an exe (Python required).
cd /d "%~dp0"
python obsidian_share_gui.py
if errorlevel 1 pause
