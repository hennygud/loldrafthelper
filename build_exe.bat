@echo off
chcp 65001 >NUL
cd /d "%~dp0"
echo Собираю LoLDraftHelper.exe, подождите пару минут...
py -m pip install --quiet pyinstaller
py -m PyInstaller --onefile --name LoLDraftHelper --add-data "lol_draft_helper.html;." lol_bridge.py
echo.
echo Готово: файл dist\LoLDraftHelper.exe
pause
