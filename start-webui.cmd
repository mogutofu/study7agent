@echo off
rem Study7Agent WebUI launcher.
rem Uses the bundled Python 3.8.10 runtime, so no installation is required.
rem This file is intentionally ASCII-only: cmd.exe mis-parses UTF-8 text under non-UTF8 codepages.
setlocal
cd /d "%~dp0"

rem --- 0. one-click launcher ---------------------------------------------------
rem Study7Agent.exe sits next to this file: double-clicking it is now the normal
rem way to start everything (hidden server + app window + tray icon, no console).
rem This .cmd stays as the fallback. It is used when the exe is missing, and it
rem keeps working when arguments are given (e.g. --port 8899), which is what the
rem VM acceptance script and the agent's restart_service tool rely on.
if "%~1"=="" if exist "%~dp0Study7Agent.exe" (
  start "" "%~dp0Study7Agent.exe"
  endlocal
  exit /b 0
)

rem --- 1. icon: desktop shortcut + folder icon (created once, silent on failure) ---
if exist "%~dp0agent.ico" call :install_icon

rem --- 2. python runtime ---
set "PY=%~dp0python\python.exe"
if not exist "%PY%" (
  rem Development fallback: use python from PATH when the bundled runtime is absent.
  where python >nul 2>nul
  if errorlevel 1 (
    echo [error] Python was not found and the bundled runtime is missing.
    echo         A release package must contain python\python.exe next to agent.exe.
    pause
    exit /b 1
  )
  echo [info] Bundled Python not found, falling back to python on PATH.
  set "PY=python"
)

if not exist "%~dp0config\agent.json" (
  if exist "%~dp0config\agent.example.json" (
    echo [info] config\agent.json not found, copying it from agent.example.json.
    copy /y "%~dp0config\agent.example.json" "%~dp0config\agent.json" >nul
  )
)

rem --- 2b. runtime sanity check ---
rem Windows 7 often lacks the Universal C Runtime (KB2999226 / VC++ 2015-2022 redist).
rem Without it python.exe cannot start at all and the window just flashes.
"%PY%" -c "import sys" >nul 2>nul
if errorlevel 1 (
  echo [error] The bundled Python could not start.
  echo.
  echo         On Windows 7 this usually means the Universal C Runtime is missing.
  echo         Install one of these, then run this file again:
  echo           * Microsoft Visual C++ 2015-2022 Redistributable ^(x86 and x64^)
  echo           * Windows update KB2999226
  echo.
  pause
  exit /b 1
)

rem --- 2c. folder completeness check ---
rem Partial extraction (dragging only some files out of the zip) leaves python\ in place but
rem no webui\; server.py then exits with code 2 and the reason is not obvious.
if not exist "%~dp0webui\server.py" (
  echo [error] webui\server.py is missing: this copy was not fully extracted.
  echo.
  echo         Extract the WHOLE zip and keep the folder structure; do not copy single files.
  echo         This folder must contain: agent.exe, agent.ico, config, docs, python,
  echo         README.md, skills, start-webui.cmd, webui
  pause
  exit /b 1
)

echo Starting Study7Agent WebUI ...
"%PY%" "%~dp0webui\server.py" %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo [error] WebUI exited with code %CODE%.
  echo         Details are printed above; a screenshot of this window is enough to diagnose.
  echo.
  pause
)
endlocal
exit /b

:install_icon
rem Desktop shortcut for this launcher, with agent.ico as its icon.
if not exist "%USERPROFILE%\Desktop\Study7Agent.lnk" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut('%USERPROFILE%\Desktop\Study7Agent.lnk'); $s.TargetPath='%~dp0start-webui.cmd'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0agent.ico'; $s.Description='Study7Agent'; $s.Save()" >nul 2>nul
)
rem Folder icon: Explorer reads IconResource from desktop.ini; the folder needs the read-only flag.
if not exist "%~dp0desktop.ini" (
  >"%~dp0desktop.ini" echo [.ShellClassInfo]
  >>"%~dp0desktop.ini" echo IconResource=agent.ico,0
  attrib +h +s "%~dp0desktop.ini" >nul 2>nul
  attrib +r "%~dp0." >nul 2>nul
)
goto :eof
