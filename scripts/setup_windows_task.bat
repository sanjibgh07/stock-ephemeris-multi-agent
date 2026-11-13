@echo off
REM ===========================================================================
REM  setup_windows_task.bat - roz 6:30 PM IST par auto-update task banao
REM  (Creates a Windows Task Scheduler job that runs the daily update.)
REM
REM  Ise ek baar double-click karo. Bas.
REM  Ye file C: drive par KUCH NAHI likhti - sab D: (project folder) me rehta hai.
REM ===========================================================================
setlocal EnableDelayedExpansion

set "PROJECT_ROOT=D:\StockEphemerisProject"

REM --- .env se PROJECT_ROOT padho (agar user ne drive badli ho) ---------------
if exist "%PROJECT_ROOT%\.env" (
  for /f "usebackq tokens=1,* delims==" %%A in ("%PROJECT_ROOT%\.env") do (
    if /i "%%A"=="PROJECT_ROOT" set "PROJECT_ROOT=%%B"
  )
)

if not exist "%PROJECT_ROOT%" (
  echo [ERROR] Project folder not found: %PROJECT_ROOT%
  echo         Edit the PROJECT_ROOT line inside this .bat file.
  pause
  exit /b 2
)

REM --- python dhoondo ----------------------------------------------------------
set "PYEXE="
where python >nul 2>nul && for /f "delims=" %%P in ('where python') do if not defined PYEXE set "PYEXE=%%P"
if not defined PYEXE (
  where py >nul 2>nul && for /f "delims=" %%P in ('where py') do if not defined PYEXE set "PYEXE=%%P -3"
)
if not defined PYEXE (
  echo [ERROR] Python not found in PATH. Install Python first.
  pause
  exit /b 3
)

echo Project root : %PROJECT_ROOT%
echo Python       : %PYEXE%
echo.

REM --- environment: cache/temp project ke andar (C: par kuch nahi) ------------
set "TMP=%PROJECT_ROOT%\.cache\tmp"
set "TEMP=%PROJECT_ROOT%\.cache\tmp"
set "PIP_CACHE_DIR=%PROJECT_ROOT%\.cache\pip"
if not exist "%TMP%" mkdir "%TMP%"

set "TASK_NAME=StockEphemerisDailyUpdate"
set "RUN_TIME=18:30"

echo Creating scheduled task "%TASK_NAME%" to run daily at %RUN_TIME% ...
schtasks /Create ^
  /TN "%TASK_NAME%" ^
  /TR "cmd /c cd /d \"%PROJECT_ROOT%\" && \"%PYEXE%\" scripts\daily_update.py --days-back 10 >> \"%PROJECT_ROOT%\logs\task_daily.log\" 2>&1" ^
  /SC DAILY ^
  /ST %RUN_TIME% ^
  /F

if errorlevel 1 (
  echo.
  echo [WARN] Task creation failed. Right-click this file and choose
  echo        "Run as administrator" and try again.
) else (
  echo.
  echo [OK] Daily task created at %RUN_TIME%.
  echo      Task name: %TASK_NAME%
  echo      Log file : %PROJECT_ROOT%\logs\task_daily.log
)

echo.
echo Useful commands:
echo   schtasks /Query  /TN "%TASK_NAME%" /V /FO LIST
echo   schtasks /Run    /TN "%TASK_NAME%"          (run now, to test)
echo   schtasks /Delete /TN "%TASK_NAME%" /F       (remove the task)
echo.
pause
endlocal
