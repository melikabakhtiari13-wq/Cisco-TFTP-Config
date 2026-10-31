@echo off
REM Builds dist\CiscoConfigTool.exe  (run on a Windows machine that has Python)
python -m pip install --upgrade pyinstaller
python -m PyInstaller --onefile --name CiscoConfigTool cisco_config_tool.py
echo.
echo Done. Output: dist\CiscoConfigTool.exe
pause
