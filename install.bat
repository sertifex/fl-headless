@echo off
chcp 65001 >nul
setlocal
if not exist "%~dp0install.py" (
  echo install.py isn't next to this file. Extract the whole fl-headless zip into a folder first ^(right-click ^> Extract All^), then run install.bat from there.
  pause
  exit /b 1
)
rem fl-headless one-click installer: finds Python 3.10+ and runs install.py (arguments are passed through).
rem No Python (or a too old one): offers to install Python 3.12 for this user with winget (Windows' own installer).
call :find
if defined PY goto run
:nopython
echo Python 3.10 or newer was not found.
where winget >nul 2>nul || goto manual
set /p ANS=Install Python 3.12 now with winget (free, from python.org, only for your user)? [y/N]: 
if /i not "%ANS%"=="y" goto manual
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
set PY=
call :find
if defined PY goto run
:manual
echo Install Python from python.org (tick "Add python.exe to PATH"), then run install.bat again.
pause
exit /b 1
:run
"%PY%" "%~dp0install.py" %*
if errorlevel 3 if not errorlevel 4 (set PY=& goto nopython)
exit /b %errorlevel%

:find
rem (-X utf8: a path with accents reaches set /p intact under chcp 65001)
rem the newest usable Python: the py launcher, python on PATH, then per-user installs (no for /f: its quoting
rem breaks on paths with spaces)
set PY=
set "PYTXT=%TEMP%\flh_python_path.txt"
py -3 -X utf8 -c "import sys;print(sys.executable if sys.version_info>=(3,10) else '')" > "%PYTXT%" 2>nul && set /p PY=<"%PYTXT%"
if defined PY goto found
python -X utf8 -c "import sys;print(sys.executable if sys.version_info>=(3,10) else '')" > "%PYTXT%" 2>nul && set /p PY=<"%PYTXT%"
if defined PY goto found
for /d %%P in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%P\python.exe" call :try "%%P\python.exe"
:found
del "%PYTXT%" >nul 2>nul
exit /b 0

:try
rem keep %1 only if it is Python 3.10+ (the last good one in folder order: Python313 after Python310, Python39 fails)
"%~1" -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=%~1"
exit /b 0
