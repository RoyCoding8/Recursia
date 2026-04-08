@echo off
setlocal

title Recursia Launcher
pushd "%~dp0" >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERROR] Could not open the project folder.
  echo Please move this file back into the Recursia folder and try again.
  echo.
  pause
  exit /b 1
)

set "PROJECT_ROOT=%CD%"
set "BACKEND_DIR=%PROJECT_ROOT%\backend"
set "FRONTEND_DIR=%PROJECT_ROOT%\frontend"
set "BACKEND_PROJECT_FILE=%BACKEND_DIR%\pyproject.toml"
set "BACKEND_LOCK_FILE=%BACKEND_DIR%\uv.lock"
set "BACKEND_ENV_FILE=%BACKEND_DIR%\.env"
set "API_URL=http://127.0.0.1:8000"
set "APP_URL=http://127.0.0.1:3000"
set "FORCE_STUB_MODE=0"
set "READY_TIMEOUT_SECONDS=25"

goto :menu

:menu
cls
echo Recursia Launcher
echo.
echo Project Folder: %PROJECT_ROOT%
if "%FORCE_STUB_MODE%"=="1" (
  echo Mode: Deterministic local demo ^(LLM_PROVIDER=stub^)
) else (
  echo Mode: Standard ^(uses backend .env / OS environment^)
)
echo.
echo Pick an option:
echo   1^) First-time setup (install everything)
echo   2^) Start full app (backend + frontend)
echo   3^) Start backend only
echo   4^) Start frontend only
echo   5^) Open app in browser
echo   6^) Exit
echo   7^) Toggle deterministic local mode
echo.
set "CHOICE="
set /p "CHOICE=Enter 1-7 and press Enter: "

if "%CHOICE%"=="1" goto :setup
if "%CHOICE%"=="2" goto :start_full
if "%CHOICE%"=="3" goto :start_backend
if "%CHOICE%"=="4" goto :start_frontend
if "%CHOICE%"=="5" goto :open_browser
if "%CHOICE%"=="6" goto :goodbye
if "%CHOICE%"=="7" goto :toggle_mode

echo.
echo [Tip] Please enter only 1, 2, 3, 4, 5, 6, or 7.
echo.
pause
goto :menu

:toggle_mode
if "%FORCE_STUB_MODE%"=="1" (
  set "FORCE_STUB_MODE=0"
  echo.
  echo Deterministic local mode is now OFF.
  echo Backend will use your existing environment and backend/.env values.
) else (
  set "FORCE_STUB_MODE=1"
  echo.
  echo Deterministic local mode is now ON.
  echo Backend windows launched from this menu will force LLM_PROVIDER=stub.
)
echo.
pause
goto :menu

:port_in_use
set "%~2=0"
netstat -ano | findstr /R /C:":%~1 .*LISTENING" >nul 2>&1
if errorlevel 1 (
  rem no listener on this port
) else (
  set "%~2=1"
)
exit /b 0

:get_port_pid
set "%~2="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%~1 .*LISTENING"') do (
  set "%~2=%%P"
  goto :eof
)
goto :eof

:port_busy_message
set "_PORT=%~1"
set "_SERVICE=%~2"
set "_PID="
call :get_port_pid %_PORT% _PID
echo.
echo [Port in use] %_SERVICE% needs port %_PORT%, but it is already occupied.
if defined _PID echo Detected listener PID: %_PID%
echo.
echo Friendly options:
echo   - Close the app currently using port %_PORT% and try again.
echo   - Use menu option 3 or 4 to start only one side for troubleshooting.
echo   - If this is an old Recursia window, close it first.
echo.
goto :eof

:wait_for_backend_ready
set "BACKEND_READY_FAILED=0"
echo.
echo Waiting for backend readiness at %API_URL%/ready (up to %READY_TIMEOUT_SECONDS%s)...
for /l %%S in (1,1,%READY_TIMEOUT_SECONDS%) do (
  powershell -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%API_URL%/ready' -TimeoutSec 2; if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300) { exit 0 } else { exit 1 } } catch { exit 1 }"
  if not errorlevel 1 (
    echo [OK] Backend is ready.
    goto :eof
  )
  <nul set /p "=."
  timeout /t 1 /nobreak >nul
)
echo.
echo [ERROR] Backend is not ready after %READY_TIMEOUT_SECONDS% seconds.
powershell -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%API_URL%/ready' -TimeoutSec 2; Write-Host ('Last /ready response: HTTP ' + [int]$r.StatusCode) } catch { if ($_.Exception.Response) { Write-Host ('Last /ready response: HTTP ' + [int]$_.Exception.Response.StatusCode.value__) } else { Write-Host ('Last /ready probe error: ' + $_.Exception.Message) } }"
echo Do not continue to frontend/browser until backend is healthy.
echo Check the "Recursia Backend" window for the exact startup error.
set "BACKEND_READY_FAILED=1"
goto :eof

:check_prereqs
set "PY_CMD="
set "HAVE_UV="
set "HAVE_NPM="

where uv >nul 2>&1
if not errorlevel 1 set "HAVE_UV=1"

where py >nul 2>&1
if not errorlevel 1 set "PY_CMD=py"

if not defined PY_CMD (
  where python >nul 2>&1
  if not errorlevel 1 set "PY_CMD=python"
)

where npm >nul 2>&1
if not errorlevel 1 set "HAVE_NPM=1"

if not defined HAVE_UV (
  echo.
  echo [Missing] uv was not found.
  echo Please install uv from https://docs.astral.sh/uv/getting-started/installation/
)

if not defined HAVE_NPM (
  echo.
  echo [Missing] npm was not found.
  echo Please install Node.js LTS from https://nodejs.org/
)

if not defined PY_CMD (
  echo.
  echo [Optional] Python executable was not found on PATH.
  echo That's okay if uv is installed - uv can manage Python for this project.
  echo If needed manually, install Python 3.11+ from https://www.python.org/downloads/
)

if not defined HAVE_UV goto :prereq_fail
if not defined HAVE_NPM goto :prereq_fail
goto :eof

:prereq_fail
echo.
echo [Action needed] Install missing required tools, then run this launcher again.
echo.
pause
goto :menu

:setup
cls
echo ==================================================
echo First-time setup
echo ==================================================
call :check_prereqs

if not exist "%BACKEND_DIR%" (
  echo.
  echo [ERROR] Backend folder not found: "%BACKEND_DIR%"
  echo.
  pause
  goto :menu
)

if not exist "%BACKEND_PROJECT_FILE%" (
  echo.
  echo [ERROR] Backend pyproject.toml not found: "%BACKEND_PROJECT_FILE%"
  echo.
  pause
  goto :menu
)

if not exist "%BACKEND_LOCK_FILE%" (
  echo.
  echo [ERROR] Backend uv.lock not found: "%BACKEND_LOCK_FILE%"
  echo.
  pause
  goto :menu
)

if not exist "%FRONTEND_DIR%" (
  echo.
  echo [ERROR] Frontend folder not found: "%FRONTEND_DIR%"
  echo.
  pause
  goto :menu
)

echo.
echo [1/2] Syncing backend dependencies with uv...
pushd "%BACKEND_DIR%" >nul
uv sync
if errorlevel 1 (
  popd >nul
  echo.
  echo [ERROR] Backend uv sync failed.
  echo.
  pause
  goto :menu
)
popd >nul

echo [2/2] Installing frontend dependencies...
pushd "%FRONTEND_DIR%" >nul
npm install
if errorlevel 1 (
  popd >nul
  echo.
  echo [ERROR] Frontend install failed.
  echo.
  pause
  goto :menu
)
popd >nul

echo.
echo Setup complete. You can now start the app.
echo.
pause
goto :menu

:ensure_backend_ready
if not exist "%BACKEND_PROJECT_FILE%" (
  echo.
  echo [Not ready] Backend pyproject.toml is missing.
  echo Cannot start backend without backend project files.
  echo.
  pause
  goto :menu
)

if not exist "%BACKEND_LOCK_FILE%" (
  echo.
  echo [Not ready] Backend uv.lock is missing.
  echo Please restore backend project files and try again.
  echo.
  pause
  goto :menu
)
goto :eof

:start_backend
cls
echo ==================================================
echo Start backend only
echo ==================================================
call :check_prereqs
call :ensure_backend_ready

echo.
echo Starting backend in a new window...
echo API URL: %API_URL%
echo Note: backend auto-loads %BACKEND_ENV_FILE% (existing OS env vars take precedence).
if not exist "%BACKEND_ENV_FILE%" echo [Warning] %BACKEND_ENV_FILE% not found - defaults/external env will be used.
call :port_in_use 8000 BACKEND_PORT_BUSY
if "%BACKEND_PORT_BUSY%"=="1" (
  call :port_busy_message 8000 Backend
  pause
  goto :menu
)
if "%FORCE_STUB_MODE%"=="1" (
  echo Deterministic local mode ON: forcing LLM_PROVIDER=stub for this backend window.
  start "Recursia Backend" /D "%BACKEND_DIR%" cmd /k "set LLM_PROVIDER=stub && uv run uvicorn main:app --host 127.0.0.1 --port 8000"
) else (
  start "Recursia Backend" /D "%BACKEND_DIR%" cmd /k "uv run uvicorn main:app --host 127.0.0.1 --port 8000"
)
call :wait_for_backend_ready
if "%BACKEND_READY_FAILED%"=="1" (
  echo.
  echo Backend window launched, but readiness failed.
  echo Check the "Recursia Backend" window for detailed error output.
  echo.
  pause
  goto :menu
)

echo Backend window opened and is ready.
echo.
pause
goto :menu

:start_frontend
cls
echo ==================================================
echo Start frontend only
echo ==================================================
call :check_prereqs

if not exist "%FRONTEND_DIR%\package.json" (
  echo.
  echo [ERROR] Frontend folder or package.json not found.
  echo.
  pause
  goto :menu
)

echo.
echo Starting frontend in a new window...
echo App URL: %APP_URL%
call :port_in_use 3000 FRONTEND_PORT_BUSY
if "%FRONTEND_PORT_BUSY%"=="1" (
  call :port_busy_message 3000 Frontend
  pause
  goto :menu
)
start "Recursia Frontend" /D "%FRONTEND_DIR%" cmd /k "set NEXT_PUBLIC_API_BASE_URL=%API_URL% && npm run dev -- -H 127.0.0.1 -p 3000"
echo Frontend window opened.
echo.
pause
goto :menu

:start_full
cls
echo ==================================================
echo Start full app (backend + frontend)
echo ==================================================
call :check_prereqs
call :ensure_backend_ready

if not exist "%FRONTEND_DIR%\package.json" (
  echo.
  echo [ERROR] Frontend folder or package.json not found.
  echo.
  pause
  goto :menu
)

call :port_in_use 8000 BACKEND_PORT_BUSY
if "%BACKEND_PORT_BUSY%"=="1" (
  call :port_busy_message 8000 Backend
  pause
  goto :menu
)

call :port_in_use 3000 FRONTEND_PORT_BUSY
if "%FRONTEND_PORT_BUSY%"=="1" (
  call :port_busy_message 3000 Frontend
  pause
  goto :menu
)

echo.
echo Starting backend in a new window...
echo Note: backend auto-loads %BACKEND_ENV_FILE% (existing OS env vars take precedence).
if not exist "%BACKEND_ENV_FILE%" echo [Warning] %BACKEND_ENV_FILE% not found - defaults/external env will be used.
if "%FORCE_STUB_MODE%"=="1" (
  echo Deterministic local mode ON: forcing LLM_PROVIDER=stub for this backend window.
  start "Recursia Backend" /D "%BACKEND_DIR%" cmd /k "set LLM_PROVIDER=stub && uv run uvicorn main:app --host 127.0.0.1 --port 8000"
) else (
  start "Recursia Backend" /D "%BACKEND_DIR%" cmd /k "uv run uvicorn main:app --host 127.0.0.1 --port 8000"
)

call :wait_for_backend_ready
if "%BACKEND_READY_FAILED%"=="1" (
  echo.
  echo Frontend/browser were NOT opened automatically because backend is not ready.
  echo.
  pause
  goto :menu
)

echo Starting frontend in a new window...
start "Recursia Frontend" /D "%FRONTEND_DIR%" cmd /k "set NEXT_PUBLIC_API_BASE_URL=%API_URL% && npm run dev -- -H 127.0.0.1 -p 3000"

echo Opening app in your browser...
start "" "%APP_URL%"

echo.
echo Done! If the page is still loading, wait a few seconds and refresh.
echo.
pause
goto :menu

:open_browser
cls
echo Opening %APP_URL% ...
start "" "%APP_URL%"
if errorlevel 1 (
  echo.
  echo [ERROR] Could not open browser automatically.
  echo Please open this URL manually: %APP_URL%
  echo.
  pause
  goto :menu
)

echo Browser opened.
echo.
pause
goto :menu

:goodbye
echo.
echo Thanks! Closing launcher.
popd >nul
endlocal
exit /b 0
