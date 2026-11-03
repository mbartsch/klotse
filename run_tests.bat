@echo off
rem Run the offline test suite. Creates .venv and installs requirements-dev.txt on first run.
rem   run_tests.bat                        run the full suite
rem   run_tests.bat --install              (re)install dependencies, then run
rem   run_tests.bat tests\test_app.py -k pods   any other arguments go to pytest
setlocal
cd /d "%~dp0"

set "VENV=.venv"
set "PY=%VENV%\Scripts\python.exe"
set "MARKER=%VENV%\.dev-deps-installed"
set "FORCE=0"
set "ARGS="
:parse
if "%~1"=="" goto parsed
if /I "%~1"=="--install" (set "FORCE=1") else (set ARGS=%ARGS% %1)
shift
goto parse
:parsed

if exist "%PY%" goto have_venv
echo Creating virtual environment in %VENV% ...
where py >nul 2>nul && (py -3 -m venv "%VENV%") || (python -m venv "%VENV%")
if errorlevel 1 (
  echo ERROR: could not create a virtual environment. Install Python 3.10+ from https://www.python.org/downloads/
  exit /b 1
)
set "FORCE=1"

:have_venv
if not exist "%MARKER%" set "FORCE=1"
if not "%FORCE%"=="1" goto run
echo Installing dependencies from requirements-dev.txt ...
"%PY%" -m pip install --disable-pip-version-check -r requirements-dev.txt
if errorlevel 1 (
  echo ERROR: dependency installation failed.
  exit /b 1
)
echo installed> "%MARKER%"

:run
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
set "QT_QPA_PLATFORM=offscreen"
"%PY%" -m pytest -q%ARGS%
exit /b %errorlevel%
