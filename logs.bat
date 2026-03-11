@echo off
setlocal

set "KEY=%USERPROFILE%\.ssh\lightsail.pem"
set "HOST=3.113.127.7"
set "REMOTE_USER=ubuntu"
set "SERVICE_NAME=vclip-scheduler"
set "LINES=200"

if not "%~1"=="" set "LINES=%~1"

echo [INFO] Showing last %LINES% lines for %SERVICE_NAME%
ssh -o IdentitiesOnly=yes -i "%KEY%" %REMOTE_USER%@%HOST% "sudo journalctl -u %SERVICE_NAME% -n %LINES% --no-pager"
if errorlevel 1 (
  echo [ERROR] Log fetch failed.
  exit /b 1
)

endlocal
