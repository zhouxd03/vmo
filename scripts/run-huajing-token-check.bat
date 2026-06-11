@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."
set "OUT_FILE=%PROJECT_DIR%\token-result.json"
set "DEFAULT_ROOT=%APPDATA%\comic-generator-electron"

cd /d "%PROJECT_DIR%"

echo ===============================================
echo Huajing token one-click scanner
echo ===============================================
echo.
echo Default scan root:
echo   %DEFAULT_ROOT%
echo.
echo Output:
echo   %OUT_FILE%
echo.
echo If this is a copied folder from another device, drag the
echo comic-generator-electron folder onto this bat file instead.
echo.

set "SCAN_ROOT=%~1"
if "%SCAN_ROOT%"=="" set "SCAN_ROOT=%DEFAULT_ROOT%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%get-huajing-token.ps1" -SearchRoot "%SCAN_ROOT%" -Reveal -VerifyRefresh -OutFile "%OUT_FILE%"

echo.
echo Done. Open token-result.json in the project folder.
echo.
pause
