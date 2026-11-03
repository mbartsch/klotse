@echo off
rem Launch Klotse. Creates .venv and installs requirements.txt on first run.
rem   run.bat              start the app (no console window)
rem   run.bat --console    start in this console so errors/tracebacks are visible
rem   run.bat --install    (re)install dependencies, then start
rem   run.bat --check      verify the dependencies import, then exit
setlocal
cd /d "%~dp0"

set "VENV=.venv"
set "PY=%VENV%\Scripts\python.exe"
set "MARKER=%VENV%\.deps-installed"
set "CONSOLE=0"
set "CHECK=0"
set "FORCE=0"
for %%A in (%*) do (
  if /I "%%A"=="--console" set "CONSOLE=1"
  if /I "%%A"=="--check" set "CHECK=1"
  if /I "%%A"=="--install" set "FORCE=1"
)

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
echo Installing dependencies from requirements.txt ...
"%PY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo ERROR: dependency installation failed.
  exit /b 1
)
echo installed> "%MARKER%"

:run
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
if "%CHECK%"=="1" goto check
if "%CONSOLE%"=="1" goto console
start "" "%VENV%\Scripts\pythonw.exe" -m klotse
exit /b 0

:console
"%PY%" -m klotse
exit /b %errorlevel%

:check
"%PY%" -c "import klotse.ui.main_window, kubernetes, yaml; print('Klotse dependencies OK')"
exit /b %errorlevel%
