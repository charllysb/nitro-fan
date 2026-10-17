@echo off
rem Gera dist\NitroFan.exe (precisa de: python -m pip install pyinstaller)
cd /d "%~dp0"
python -m PyInstaller --onefile --windowed --uac-admin --name NitroFan --clean --noconfirm nitro_fan_gui.pyw
