@echo off
chcp 65001 >NUL
cd /d "%~dp0"
echo Собираю LoLDraftHelper.exe, подождите пару минут...
py -m pip install --quiet pyinstaller pywebview
py -m PyInstaller --onefile --windowed --name LoLDraftHelper --add-data "lol_draft_helper.html;." --collect-all webview --collect-all clr_loader --collect-all pythonnet --hidden-import clr lol_bridge.py
echo.
echo Готово: файл dist\LoLDraftHelper.exe
pause
