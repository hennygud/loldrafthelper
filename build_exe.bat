@echo off
setlocal
cd /d "%~dp0"
set "MLK_PYTHON=py"
if not "%~1"=="" set "MLK_PYTHON=%~1"

"%MLK_PYTHON%" -m pip install pyinstaller pywebview
if errorlevel 1 exit /b 1
set "STATS="
if exist stats.json set "STATS=--add-data stats.json;."
"%MLK_PYTHON%" -m PyInstaller --onefile --windowed --name MLK.gg --icon assets/img/logo.ico --add-data "index.html;." --add-data "assets;assets" %STATS% --collect-all webview --collect-all clr_loader --collect-all pythonnet --hidden-import clr lol_bridge.py
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\package-windows.ps1"
if errorlevel 1 exit /b 1
echo MLK.gg: dist\MLK.gg-Windows.zip
if "%~1"=="" pause
