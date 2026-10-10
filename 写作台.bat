@echo off
chcp 936 >nul
rem ══════════════════════════════════════════════════════════════
rem 写作台 · 启动器（不带控制台窗口，像桌面应用那样）
rem 出错了看不到提示 —— 所以输出都写进「运行日志.txt」，起不来先看那个。
rem ══════════════════════════════════════════════════════════════
setlocal
set "APPDIR=%~dp0"
set "PY="
for %%I in (python.exe) do if not defined PY if exist "%%~$PATH:I" set "PY=%%~$PATH:I"
if not defined PY set "PY=py -3"
set "PYW="
for %%I in (pythonw.exe) do if not defined PYW if exist "%%~$PATH:I" set "PYW=%%~$PATH:I"
if not defined PYW set "PYW=%%PY%%"
if not exist "%APPDIR%app.py" goto :noapp
if not defined PYW goto :nopy
start "写作台" "%PYW%" "%APPDIR%app.py" 1>>"%APPDIR%运行日志.txt" 2>&1
exit /b 0

:noapp
echo 找不到 app.py —— 这个文件要放在写作台目录里（现在在 %APPDIR%）。
echo.
pause
exit /b 1

:nopy
echo 找不到 Python —— 请先装 Python 3.10 或更高版本并加入 PATH。

echo.
pause
exit /b 1
