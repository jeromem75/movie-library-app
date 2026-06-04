@echo off
setlocal
cd /d "%~dp0"

set "RESTART_EXIT_CODE=75"

:run
python web_app.py
set "APP_EXIT_CODE=%ERRORLEVEL%"

if "%APP_EXIT_CODE%"=="%RESTART_EXIT_CODE%" (
    echo.
    echo Restarting web server in the same window...
    timeout /t 1 /nobreak >nul
    goto run
)

echo.
echo Web server stopped with exit code %APP_EXIT_CODE%.
pause
