@echo off
rem  Subtitle Studio 一键启动（双击即可）
chcp 65001 >nul
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw run.py %*
) else (
  start "" python run.py %*
)
