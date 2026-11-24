@echo off
rem Regression check of a Windows build against the recorded Linux reference (needs Python 3 only).
rem usage:  verify.bat [path\to\zeval.exe]        (default: zeval.exe next to this file, else dist\zeval.exe of build\build.bat)
setlocal
cd /d "%~dp0"
set "ENGINE=%~1"
if "%ENGINE%"=="" set "ENGINE=%~dp0zeval.exe"
if "%~1"=="" if not exist "%ENGINE%" set "ENGINE=%~dp0dist\zeval.exe"
if not exist "%ENGINE%" (echo ERROR: engine not found: %ENGINE%  - build it first with build\build.bat & exit /b 1)
echo === 1/5 golden search comparison vs Linux reference (60 positions, depth 8)
python tests\t_golden.py || exit /b 1
echo === 2/5 robustness (piped commands, EOF, go infinite, long lines, console mode, malformed FEN)
python tests\t_robust.py || exit /b 1
echo === 3/5 UCI output: full pv, ponder, seldepth, hashfull, currmove (needs python-chess)
python tests\t_pv.py || exit /b 1
echo === 4/5 time controls / stop latency / isready during a search (every case must return a bestmove)
python tests\t_timecontrol.py || exit /b 1
echo === 5/5 real-clock time management: 3 short games, every move legal, no flag fall (go wtime/btime/winc/binc)
python tests\t_tmclock.py "%ENGINE%" --games 3 --plies 30 --base 3000 --inc 30 || exit /b 1
echo.
echo ALL CHECKS PASSED
