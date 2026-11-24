@echo off
rem Zeval tactics suite - easy start (Windows).  Linux / WSL: ./tactics.sh.   Help: tactics.bat with no argument
rem
rem   tactics.bat check                       environment + engine + suite files
rem   tactics.bat selftest                    wiring regression test (~20 s)
rem   tactics.bat smoke                       wac + mates @ 200 ms (~1 min)  tripwire after any code change
rem   tactics.bat standard                    5 discriminating suites @ 1000 ms (~11 min)
rem   tactics.bat full                        all 7 suites @ 1000 ms (~19 min, protocol of the recorded baseline)
rem   tactics.bat ab ENGINE_A ENGINE_B        alternating A/B on this machine + analysis   [--profile smoke^|standard^|full]
rem   tactics.bat baseline ^| list ^| analyze TAG NAME_A NAME_B
rem Options of smoke / standard / full:  -e ENGINE  -n NAME  -t MOVETIME_MS  --suites a,b  --hash MB  --fresh
rem Default engine = zeval.exe next to this file, else dist\zeval.exe (environment variable ENGINE overrides).  Needs Python 3.8+ (python.org);  pip install chess  for the analysis.
rem 'variant' (building engine variants) is Linux only; on Windows build candidates with build\build.bat and pass the exe to  ab.
rem NOTE: written and checked on Linux only (the python code is portable, this wrapper was not run on Windows).
setlocal
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (echo tactics.bat: Python 3 not found - install it from python.org & exit /b 1)
if "%~1"=="" (%PY% tactics\tools\tactics.py --help & exit /b 0)
%PY% tactics\tools\tactics.py %*
exit /b %errorlevel%
