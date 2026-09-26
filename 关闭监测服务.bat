@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo 正在关闭小菜蛾监测服务...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_monitor_server.ps1"
set "STOP_RESULT=%ERRORLEVEL%"
echo.
if "%STOP_RESULT%"=="0" (
  echo 关闭操作完成。
) else (
  echo 关闭操作失败，请把本窗口中的错误信息发给开发者。
)
pause
exit /b %STOP_RESULT%
