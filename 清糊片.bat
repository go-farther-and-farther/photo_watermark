@echo off
setlocal
title Blur Cull

if "%~1"=="" goto usage

if not exist "%~1\" (
    echo Folder not found: %~1
    pause
    exit /b 1
)

set "PY=py -3"
where py >nul 2>nul || set "PY=python"

%PY% "%~dp0blur_cull.py" "%~1" --ask-after --confirm
echo.
pause
exit /b 0

:usage
py -3 "%~dp0blur_cull.py" --usage
pause
exit /b 1