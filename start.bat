@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto deps
py -3.11 -m venv .venv 2>nul
if exist ".venv\Scripts\python.exe" goto deps
py -3.12 -m venv .venv 2>nul
if exist ".venv\Scripts\python.exe" goto deps
echo Install Python 3.11 or 3.12 from python.org, with the Python launcher enabled.
echo Then double-click start.bat again. See README.md for manual setup.
pause
exit /b 1
:deps
".venv\Scripts\python.exe" -c "from PySide6.QtWidgets import QApplication; import pydantic_ai; import netpet_tools" >nul 2>nul
if not errorlevel 1 goto run
".venv\Scripts\python.exe" -m pip install -r requirements.txt -e Tools
if errorlevel 1 goto failed
:run
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Keep this window open and read the error above.
pause
exit /b 1
