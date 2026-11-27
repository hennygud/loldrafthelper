@echo off
chcp 65001 >NUL
cd /d "%~dp0"
echo Собираю mlkdrafthelper.exe, подождите пару минут...
py -m pip install --quiet pyinstaller pywebview
py -m PyInstaller --onefile --windowed --name mlkdrafthelper --icon assets/mlkdrafthelper.ico --add-data "assets;assets" --add-data "lol_draft_helper.html;." --collect-all webview --collect-all clr_loader --collect-all pythonnet --hidden-import clr lol_bridge.py
echo.
echo Готово: файл dist\mlkdrafthelper.exe
pause
