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

start "" "http://%HOST%:%PORT%/"
"%PYTHON_BIN%" test_site.py
