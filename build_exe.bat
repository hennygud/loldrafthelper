@echo off
chcp 65001 >NUL
cd /d "%~dp0"
echo Собираю MLK.gg.exe, подождите пару минут...
py -m pip install --quiet pyinstaller pywebview
py -m PyInstaller --onefile --windowed --name "MLK.gg" --icon assets/mlkdrafthelper.ico --add-data "assets;assets" --add-data "lol_draft_helper.html;." --collect-all webview --collect-all clr_loader --collect-all pythonnet --hidden-import clr lol_bridge.py
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0package_windows.ps1"
if errorlevel 1 exit /b 1
echo.
echo Готово: файл dist\MLK.gg.exe
pause
