@echo off
setlocal

cd /d "%~dp0"

set "SCRIPT=scripts\simulation\sim_gui.py"
set "HOST=127.0.0.1"
set "PORT=8765"

if not exist "%SCRIPT%" (
  echo [ERROR] %SCRIPT% not found.
  pause
  exit /b 1
)

set "RUNNER_EXE="
set "RUNNER_ARG="
if exist ".postgresql\pgsql\pgAdmin 4\python\python.exe" (
  ".postgresql\pgsql\pgAdmin 4\python\python.exe" -c "import sys; print(sys.version)" >nul 2>&1
  if %errorlevel%==0 set "RUNNER_EXE=.postgresql\pgsql\pgAdmin 4\python\python.exe"
)
if not defined RUNNER_EXE if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe -c "import sys; print(sys.version)" >nul 2>&1
  if %errorlevel%==0 set "RUNNER_EXE=.venv\Scripts\python.exe"
)
if not defined RUNNER_EXE (
  where python >nul 2>&1
  if %errorlevel%==0 (
    python -c "import sys; print(sys.version)" >nul 2>&1
    if %errorlevel%==0 set "RUNNER_EXE=python"
  )
)
if not defined RUNNER_EXE (
  where py >nul 2>&1
  if %errorlevel%==0 (
    py -3 -c "import sys; print(sys.version)" >nul 2>&1
    if %errorlevel%==0 (
      set "RUNNER_EXE=py"
      set "RUNNER_ARG=-3"
    )
  )
)

if not defined RUNNER_EXE (
  echo [ERROR] Usable Python executable not found.
  echo         .venv\Scripts\python.exe is missing or broken, and python/py is not in PATH.
  echo.
  echo [Hint] Recreate virtualenv or install Python, then retry.
  pause
  exit /b 1
)

echo [INFO] Starting simulation GUI with: %RUNNER_EXE% %RUNNER_ARG%
start "Simulation GUI" cmd /k ""%RUNNER_EXE%" %RUNNER_ARG% "%SCRIPT%""

set /a WAIT_SECS=0
:wait_loop
powershell -NoProfile -Command "try { $c = New-Object Net.Sockets.TcpClient('%HOST%', %PORT%); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel%==0 goto opened
set /a WAIT_SECS+=1
if %WAIT_SECS% GEQ 10 goto timeout
timeout /t 1 >nul
goto wait_loop

:opened
echo [INFO] GUI is running at: http://%HOST%:%PORT%
echo [INFO] Open the URL manually in your browser.
exit /b 0

:timeout
echo [ERROR] GUI server did not open port %PORT% within 10 seconds.
echo         Check the \"Simulation GUI\" console window for Python errors.
echo         (Window is kept open with cmd /k)
pause
exit /b 1
