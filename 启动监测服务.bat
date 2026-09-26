@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON_EXE="
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PYTHON_EXE (
  for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
)

if not defined PYTHON_EXE (
  echo 启动失败：没有找到 Python。
  echo 请先安装 Python 3，并勾选 Add Python to PATH。
  pause
  exit /b 1
)

echo 正在启动小菜蛾监测服务...
echo Python：%PYTHON_EXE%
"%PYTHON_EXE%" -u "%~dp0pc_server.py"
if errorlevel 1 (
  echo.
  echo 启动失败，请查看上方错误信息。
  pause
  exit /b 1
)
