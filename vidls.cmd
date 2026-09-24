@echo off
rem ===================================================================
rem  vidls -- ls / ll replacement with video attribute probing.
rem           (Windows launcher; the kernel is vidls_win.py)
rem
rem  Why two names: the kernel keeps a distinct name so it is obvious
rem  which file is the Linux one (vidls.py) and which is the Windows
rem  one (vidls_win.py).  This launcher is called vidls.cmd so that the
rem  COMMAND is `vidls` on Windows too, exactly like on Linux.
rem
rem  The real implementation is vidls_win.py next to this script.  This
rem  launcher uses %~dp0 to find its own directory, so it runs straight
rem  from the repo:   vidls.cmd        (./vidls.cmd in Git Bash)
rem
rem  To get a `vidls` command that works from any directory:
rem      vidls --install            (or: vidls.cmd --install)
rem  The installer writes launchers into a directory that is already on
rem  PATH (by default %USERPROFILE%\.local\bin).  Those launchers point
rem  back at THIS repo's vidls_win.py, so code changes take effect at
rem  once and you never reinstall -- but moving the repo does require
rem  re-running --install.
rem
rem  NOTE: this file is deliberately PURE ASCII.  cmd.exe parses batch
rem  files using the ANSI code page (936 on this machine), so UTF-8
rem  Chinese comments get mangled into garbage that breaks parsing.
rem ===================================================================
setlocal

rem  Prefer the py launcher, fall back to python on PATH.
set "PY="
for %%P in (py.exe) do if not defined PY if exist "%%~$PATH:P" set "PY=py -3"
if not defined PY for %%P in (python.exe) do if not defined PY if exist "%%~$PATH:P" set "PY=python"

if not defined PY (
    echo [ERROR] python not found ^(Python 3.8+ is required^). 1>&2
    echo         Install Python, or call the kernel directly: 1>&2
    echo             python "%~dp0vidls_win.py" ... 1>&2
    exit /b 1
)

if not exist "%~dp0vidls_win.py" (
    echo [ERROR] "%~dp0vidls_win.py" not found - it must sit next to this script. 1>&2
    exit /b 1
)

%PY% "%~dp0vidls_win.py" %*
exit /b %ERRORLEVEL%
