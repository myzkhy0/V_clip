@echo off
setlocal

cd /d "%~dp0"

if not exist ".postgresql\pgsql\bin\pg_ctl.exe" (
  echo [ERROR] Local PostgreSQL binaries were not found.
  pause
  exit /b 1
)

if not exist ".postgresql\data\PG_VERSION" (
  echo [ERROR] Local PostgreSQL data directory was not initialized.
  pause
  exit /b 1
)

echo Stopping local PostgreSQL...
".postgresql\pgsql\bin\pg_ctl.exe" -D ".postgresql\data" stop
if errorlevel 1 (
  echo [ERROR] Failed to stop local PostgreSQL.
  pause
  exit /b 1
)

echo Local PostgreSQL stopped.
endlocal
