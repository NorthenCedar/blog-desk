@echo off
chcp 936 >nul
rem ══════════════════════════════════════════════════════════════
rem 写作台 · 带控制台的启动器（调试用）
rem 窗口起不来、或者想看到服务端的实时输出时，用这个。
rem ══════════════════════════════════════════════════════════════
setlocal
set "APPDIR=%~dp0"
set "PY="
for %%I in (python.exe) do if not defined PY if exist "%%~$PATH:I" set "PY=%%~$PATH:I"
if not defined PY set "PY=py -3"
set "PYW="
for %%I in (pythonw.exe) do if not defined PYW if exist "%%~$PATH:I" set "PYW=%%~$PATH:I"
if not defined PYW set "PYW=%%PY%%"
cd /d "%APPDIR%"
echo 写作台 · 正在启动……
echo.
echo （关掉这个窗口就是退出写作台）
echo.
"%PY%" app.py
set "RC=%ERRORLEVEL%"
echo.
echo —— 已退出（代码 %RC%）——
if not "%RC%"=="0" echo 上面就是报错内容，把它发给帮你搭这个应用的人。
echo.
pause
