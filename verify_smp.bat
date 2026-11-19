@echo off
rem Lazy SMP check on a real Windows machine.  Needs Python 3 on PATH and python-chess (pip install chess) for step 2.
rem Uses the release zeval.exe and the prebuilt TEST build testbuild\zeval_smptest.exe (-DSMPTEST -DNOTT -DSMP_ASPSTEP=0; NOT the release).
rem usage:  verify_smp.bat                 (to keep the output:  verify_smp.bat > verify_smp_result.txt 2>&1)
setlocal
cd /d "%~dp0"
for %%f in ("%~dp0zeval.exe" "%~dp0testbuild\zeval_smptest.exe") do if not exist %%f (echo ERROR: not found: %%f & exit /b 1)
echo === machine
ver
echo %PROCESSOR_IDENTIFIER%  logical processors: %NUMBER_OF_PROCESSORS%
set GOLDEN_OPTS=
echo === 1/4 release zeval.exe, Threads=1: golden (60 positions, depth 8)
python tests\t_golden.py "%~dp0zeval.exe" || exit /b 1
echo === 2/4 Threads 1..32: option, legal moves, stop / quit / EOF, mate, 600 short searches, Threads changes, go nodes, timing; thread counts and idle CPU need psutil (pip install psutil)
python tests\t_smp.py "%~dp0zeval.exe" || exit /b 1
echo === 3/4 helper search == main thread search (test build, table off): nodes / score / best move identical, also with game history
python tests\t_smp_equiv.py --exe "%~dp0testbuild\zeval_smptest.exe" --positions 60 || exit /b 1
echo === 4/4 NPS scaling and time to depth, Threads 1 2 4 8 12 (informational, needs a quiet machine; takes about 2 minutes)
python tests\smp_scale.py "%~dp0zeval.exe" --threads 1,2,4,8,12 --ms 3000 --depth 16
echo.
echo ALL CHECKS PASSED
