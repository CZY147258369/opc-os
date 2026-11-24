@echo off
chcp 65001 >nul
title OPC 一人公司 AI 操作系统
cd /d "%~dp0"

set PORT=8770
echo.
echo   ============================================
echo     OPC 一人公司 AI 操作系统  ·  本地控制台
echo   ============================================
echo.

where python >nul 2>nul
if %errorlevel%==0 (set PY=python) else (set PY=py -3)

echo   [1/4] 检查本地模型实例（127.0.0.1:11435）...
curl -s -m 2 http://127.0.0.1:11435/api/tags >nul 2>nul
if %errorlevel%==0 (
  echo         已在线
) else (
  echo         未运行，后台拉起（模型目录 D:\ai\ollama-models）...
  start "" /min cmd /c "set OLLAMA_MODELS=D:\ai\ollama-models&& set OLLAMA_HOST=127.0.0.1:11435&& ollama serve"
  timeout /t 4 >nul
)

echo   [2/4] 检查系统 Ollama（127.0.0.1:11434）...
curl -s -m 2 http://127.0.0.1:11434/api/tags >nul 2>nul
if %errorlevel%==0 (echo         在线) else (echo         未运行，可忽略)

echo   [3/4] 启动控制台 ...
start "" /min %PY% "%~dp0server.py"

echo   [4/4] 打开界面 ...
timeout /t 3 >nul
start "" "http://127.0.0.1:%PORT%/"
echo.
echo   界面已在浏览器打开： http://127.0.0.1:%PORT%/
echo   关闭本窗口不会停止服务；要停止服务请运行 停止服务.cmd
echo.
timeout /t 8 >nul
