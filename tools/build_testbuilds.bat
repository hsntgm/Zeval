@echo off
rem Builds the TEST builds that verify_smp.bat and verify_units.bat use into testbuild\ (Windows).  They are not the release engine.
rem A release package has them in its test kit; a checkout builds them with this script (Linux: sh tools/build_testbuilds.sh).
rem Run it like build\build.bat: from an "x64 Native Tools Command Prompt" with nasm.exe on PATH.
rem    zeval_smptest.exe   -DSMPTEST -DNOTT -DSMP_ASPSTEP=0     verify_smp.bat
rem    zeval_ut.exe        -DUNITTEST                           verify_units.bat
rem    zeval_ut_nott.exe   -DUNITTEST -DNOTT                    verify_units.bat
rem    zeval_ut_plain.exe  -DUNITTEST -DTTPLAIN                 verify_units.bat (comparison only)
rem An existing dist\ is moved aside and put back at the end; zeval.asm is restored.  About 1-2 minutes per build.
setlocal
cd /d "%~dp0.."
if exist dist.tb_saved (echo ERROR: dist.tb_saved exists from an interrupted run - rename it back to dist first & exit /b 1)
if exist dist (move dist dist.tb_saved >nul || (echo ERROR: cannot move dist aside & exit /b 1))
if exist zeval.asm copy /y zeval.asm zeval.asm.tb_saved >nul
if not exist testbuild mkdir testbuild
set "RC=0"
call :one zeval_smptest "-DSMPTEST -DNOTT -DSMP_ASPSTEP=0" || set "RC=1"
if "%RC%"=="0" call :one zeval_ut "-DUNITTEST" || set "RC=1"
if "%RC%"=="0" call :one zeval_ut_nott "-DUNITTEST -DNOTT" || set "RC=1"
if "%RC%"=="0" call :one zeval_ut_plain "-DUNITTEST -DTTPLAIN" || set "RC=1"
if exist dist rmdir /s /q dist
if exist dist.tb_saved move dist.tb_saved dist >nul
if exist zeval.asm.tb_saved move /y zeval.asm.tb_saved zeval.asm >nul
if "%RC%"=="0" (echo done: testbuild\zeval_smptest.exe zeval_ut.exe zeval_ut_nott.exe zeval_ut_plain.exe) else (echo ERROR: a test build failed)
exit /b %RC%

:one
echo building testbuild\%1.exe  [%~2]
set "NASMFLAGS=%~2"
set "NUNITS=32"
call build\build.bat > "%TEMP%\zeval_tb_%1.log" 2>&1
if errorlevel 1 (echo build failed: %1 - see %TEMP%\zeval_tb_%1.log & exit /b 1)
if not exist dist\zeval.exe (echo build of %1 ended without dist\zeval.exe & exit /b 1)
copy /y dist\zeval.exe testbuild\%1.exe >nul || exit /b 1
exit /b 0
