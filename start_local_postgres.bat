@echo off
setlocal

cd /d "%~dp0"

if not exist ".postgresql\pgsql\bin\pg_ctl.exe" (
  echo [ERROR] Local PostgreSQL binaries were not found.
  echo [ERROR] Expected: .postgresql\pgsql\bin\pg_ctl.exe
  pause
  exit /b 1
)

if not exist ".postgresql\data\PG_VERSION" (
  echo [ERROR] Local PostgreSQL data directory was not initialized.
  echo [ERROR] Expected: .postgresql\data\PG_VERSION
  pause
  exit /b 1
)

echo Starting local PostgreSQL on localhost:5432...
".postgresql\pgsql\bin\pg_ctl.exe" -D ".postgresql\data" -l ".postgresql\postgres.log" start
if errorlevel 1 (
  echo [ERROR] Failed to start local PostgreSQL.
  pause
  exit /b 1
)

echo Local PostgreSQL started.
endlocal
