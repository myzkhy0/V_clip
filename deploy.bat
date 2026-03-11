@echo off
setlocal

set "KEY=%USERPROFILE%\.ssh\lightsail.pem"
set "HOST=3.113.127.7"
set "REMOTE_USER=ubuntu"
set "APP_DIR=/opt/vclip"
set "SERVICE_NAME=vclip-scheduler"
set "BRANCH=main"

if not "%~1"=="" set "BRANCH=%~1"

echo [INFO] Deploying to %REMOTE_USER%@%HOST% (branch=%BRANCH%)
ssh -o IdentitiesOnly=yes -i "%KEY%" %REMOTE_USER%@%HOST% "cd %APP_DIR% && APP_DIR=%APP_DIR% SERVICE_NAME=%SERVICE_NAME% BRANCH=%BRANCH% bash deploy.sh"
if errorlevel 1 (
  echo [ERROR] Deploy failed.
  exit /b 1
)

echo [OK] Deploy completed.
endlocal
