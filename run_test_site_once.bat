@echo off
setlocal

cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=8000"

if exist ".venv\Scripts\python.exe" (
  set "PYTHON_BIN=.venv\Scripts\python.exe"
) else (
  set "PYTHON_BIN=python"
)

if not exist ".env" (
  echo [ERROR] .env file was not found.
  echo [ERROR] Copy .env.example to .env and set DATABASE_URL.
  pause
  exit /b 1
)

echo [1/5] Checking PostgreSQL...
"%PYTHON_BIN%" check_postgres.py
if errorlevel 1 (
  if exist ".postgresql\pgsql\bin\pg_ctl.exe" (
    echo [INFO] Trying bundled local PostgreSQL...
    call "%~dp0start_local_postgres.bat"
    if errorlevel 1 (
      echo [ERROR] PostgreSQL check failed.
      echo [ERROR] Confirm the service is running and DATABASE_URL points to it.
      pause
      exit /b 1
    )
    "%PYTHON_BIN%" check_postgres.py
    if errorlevel 1 (
      echo [ERROR] PostgreSQL is still not reachable after startup.
      pause
      exit /b 1
    )
  ) else (
    echo [ERROR] PostgreSQL check failed.
    echo [ERROR] Confirm the service is running and DATABASE_URL points to it.
    pause
    exit /b 1
  )
)

echo [2/5] Initializing database schema...
"%PYTHON_BIN%" scheduler.py --init-db
if errorlevel 1 (
  echo [ERROR] scheduler.py --init-db failed.
  echo [ERROR] Confirm PostgreSQL is reachable and DATABASE_URL is correct.
  pause
  exit /b 1
)

echo [3/5] Running pipeline once...
"%PYTHON_BIN%" scheduler.py --once
if errorlevel 1 (
  echo [ERROR] scheduler.py --once failed.
  echo [ERROR] Confirm YOUTUBE_API_KEY and PostgreSQL settings.
  pause
  exit /b 1
)

echo [4/5] Opening browser...
start "" "http://%HOST%:%PORT%/"

echo [5/5] Starting local test site...
"%PYTHON_BIN%" test_site.py

endlocal
