@echo off
set URL=https://www.jd.com/
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0open_jd_manual.ps1" "%URL%"
pause
