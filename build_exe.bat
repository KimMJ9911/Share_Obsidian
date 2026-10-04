@echo off
rem Builds Obsidian Share GUI into a single exe.
rem Requirements: Python 3.x installed and on PATH, Git for Windows installed.

cd /d "%~dp0"

echo [1/3] Installing required packages (pyyaml, pyinstaller)...
python -m pip install --upgrade pip pyyaml pyinstaller
if errorlevel 1 goto :error

echo [2/3] Building with PyInstaller...
python -m PyInstaller --onefile --windowed --name "ObsidianShare" --noconfirm obsidian_share_gui.py
if errorlevel 1 goto :error

echo [3/3] Done.
echo Output: dist\ObsidianShare.exe
echo.
echo Wherever you move the exe, keep config.yaml next to it so it auto-loads.
echo (If config.yaml is missing, use "Browse" in the GUI to pick it manually.)
pause
exit /b 0

:error
echo.
echo Build failed. Check the log above for details.
pause
exit /b 1
