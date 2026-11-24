@echo off
chcp 65001 >nul
title 停止 OPC 服务
echo 正在停止 OPC 控制台（端口 8770）...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "127.0.0.1:8770" ^| findstr LISTENING') do (
  taskkill /F /PID %%a >nul 2>nul
)
echo 已停止。
timeout /t 3 >nul
