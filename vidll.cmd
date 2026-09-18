@echo off
rem ===================================================================
rem  vidll -- shortcut for `vidls -l`  (i.e. the ll replacement).
rem           (Windows launcher; the kernel is vidls_win.py)
rem
rem  This script contains NO logic of its own: it just prepends -l and
rem  hands off to vidls.cmd next to it.  That way option parsing, layout,
rem  exit codes and probing stay exactly the same as `vidls -l`, and
rem  changing vidls never requires syncing this file.
rem
rem      vidll              == vidls -l
rem      vidll -h           == vidls -l -h
rem      vidll -lt \path    == vidls -l -lt \path
rem
rem  Install: `vidls --install` puts BOTH vidls and vidll on PATH.
rem
rem  NOTE: pure ASCII on purpose -- cmd.exe parses batch files using the
rem  ANSI code page (936 here), so UTF-8 Chinese comments break parsing.
rem ===================================================================
setlocal

set "V=%~dp0vidls.cmd"
if not exist "%V%" (
    echo [ERROR] "%V%" not found - vidll is only a shortcut for `vidls -l`. 1>&2
    exit /b 1
)

call "%V%" -l %*
exit /b %ERRORLEVEL%
