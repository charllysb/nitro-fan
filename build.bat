@echo off
rem Gera dist\NitroFan.exe (precisa de: python -m pip install pyinstaller pystray pillow)
cd /d "%~dp0"
python -m PyInstaller --onefile --windowed --uac-admin --hidden-import pystray._win32 --exclude-module numpy --name NitroFan --clean --noconfirm nitro_fan_gui.pyw
