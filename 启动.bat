@echo off
cd /d "%~dp0"
echo 启动题库系统...
echo.
start http://localhost:8765
python -m http.server 8765
