@echo off
setlocal DisableDelayedExpansion
set "ComSpec=%SystemRoot%\System32\cmd.exe"
if "%~1"=="--help" goto help
if "%~1"=="-h" goto help
if "%~2"=="--help" goto help
if /i not "%PROCESSOR_ARCHITECTURE%"=="AMD64" if /i not "%PROCESSOR_ARCHITEW6432%"=="AMD64" goto unsupported
set "intent_archive=%~dp0vendor\portable\python\win32-x64.tar.gz"
set "intent_sha=7c45c9622400d578709a9b2cddbe8124cc21d382409d9f13406d706d28e31b14"
set "intent_runtime=%~dp0.runtime"
if not exist "%intent_runtime%" mkdir "%intent_runtime%"
if not exist "%intent_runtime%\." goto corrupt
for %%P in ("%~dp0vendor" "%~dp0vendor\portable" "%~dp0vendor\portable\python" "%intent_archive%" "%intent_runtime%" "%~dp0scripts" "%~dp0scripts\portable_bootstrap.py") do (
  "%SystemRoot%\System32\fsutil.exe" reparsepoint query "%%~P" >nul 2>&1
  if not errorlevel 1 goto corrupt
)
set "intent_stage=%intent_runtime%\bootstrap-%RANDOM%-%RANDOM%"
mkdir "%intent_stage%" 2>nul
if errorlevel 1 goto corrupt
rem No FOR /F command or CALL reparses a user-controlled checkout path.
copy /b "%intent_archive%" "%intent_stage%\python.tar.gz" >nul
if errorlevel 1 goto cleanup_error
"%SystemRoot%\System32\certutil.exe" -hashfile "%intent_stage%\python.tar.gz" SHA256 >"%intent_stage%\archive.hash" 2>nul
if errorlevel 1 goto cleanup_error
"%SystemRoot%\System32\findstr.exe" /i /x /c:"%intent_sha%" "%intent_stage%\archive.hash" >nul
if errorlevel 1 goto cleanup_error
set "TAR_OPTIONS="
"%SystemRoot%\System32\tar.exe" -xzf "%intent_stage%\python.tar.gz" -C "%intent_stage%"
if errorlevel 1 goto cleanup_error
set "PYTHONHOME="
set "PYTHONPATH="
set "__PYVENV_LAUNCHER__="
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%intent_stage%"
"%intent_stage%\python\python.exe" -I -B "%~dp0scripts\portable_bootstrap.py" %*
set "intent_result=%ERRORLEVEL%"
cd /d "%~dp0"
rmdir /s /q "%intent_stage%"
exit /b %intent_result%
:cleanup_error
rmdir /s /q "%intent_stage%"
:corrupt
echo Bundled runtime is missing, changed or linked. Use a complete clone of Intent-Slide.
exit /b 2
:unsupported
echo This launcher supports Windows x64. Windows ARM and Linux are not supported.
exit /b 2
:help
echo Intent-Slide: intent-slide.cmd [start^|setup^|doctor] [--open] [--provider auto^|codex^|claude^|gemini^|opencode]
exit /b 0
