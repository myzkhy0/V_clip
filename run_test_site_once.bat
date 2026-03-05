@echo off
setlocal

cd /d "%~dp0"

echo [INFO] run_test_site_once.bat is now a compatibility launcher.
echo [INFO] It no longer runs scheduler.py --once.
call "%~dp0open_test_site.bat"

endlocal
