@echo off
cd /d "%~dp0"
echo ================================
echo   会计题库系统 - 启动中...
echo ================================
echo.
echo   本地访问: http://localhost:8765
echo   公网访问: https://shiwen561.github.io/tiku/
echo.
echo ================================
echo.
start http://localhost:8765
python -m http.server 8765
