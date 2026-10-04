#!/bin/bash
# Builds ObsidianShare.app and packages it into ObsidianShare.dmg.
# Requirements: Python 3.x, Git (on PATH).
set -e
cd "$(dirname "$0")"

echo "[1/4] Installing required packages (pyyaml, pyinstaller)..."
python3 -m pip install --upgrade pip pyyaml pyinstaller

echo "[2/4] Building .app with PyInstaller..."
rm -rf build dist
python3 -m PyInstaller --windowed --name "ObsidianShare" --noconfirm obsidian_share_gui.py

echo "[3/4] Packaging .dmg..."
rm -rf dmg_staging ObsidianShare.dmg
mkdir dmg_staging
cp -R dist/ObsidianShare.app dmg_staging/
ln -s /Applications dmg_staging/Applications
hdiutil create -volname "ObsidianShare" -srcfolder dmg_staging -ov -format UDZO ObsidianShare.dmg
rm -rf dmg_staging

echo "[4/4] Done."
echo "Output: dist/ObsidianShare.app and ObsidianShare.dmg"
echo "Keep config.yaml next to the app (or use Browse in the GUI) to load settings."
