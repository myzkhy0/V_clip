@echo off
setlocal

set "KEY=%USERPROFILE%\.ssh\lightsail.pem"
set "HOST=3.113.127.7"
set "REMOTE_USER=ubuntu"

echo [INFO] Count videos added on 2026-03-09 (JST-window input)
ssh -o IdentitiesOnly=yes -i "%KEY%" %REMOTE_USER%@%HOST% "sudo -u postgres psql -d vclip -c \"SELECT COUNT(*) AS cnt FROM videos WHERE added_at >= TIMESTAMP '2026-03-09 00:00:00' AND added_at < TIMESTAMP '2026-03-10 00:00:00';\""
if errorlevel 1 (
  echo [ERROR] Count query failed.
  exit /b 1
)

echo [INFO] Top 20 newest rows in that window
ssh -o IdentitiesOnly=yes -i "%KEY%" %REMOTE_USER%@%HOST% "sudo -u postgres psql -d vclip -c \"SELECT video_id, title, channel_name, added_at, published_at FROM videos WHERE added_at >= TIMESTAMP '2026-03-09 00:00:00' AND added_at < TIMESTAMP '2026-03-10 00:00:00' ORDER BY added_at DESC LIMIT 20;\""
if errorlevel 1 (
  echo [ERROR] Detail query failed.
  exit /b 1
)

endlocal
