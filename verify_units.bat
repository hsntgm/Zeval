@echo off
rem Thread-unit check on a real Windows machine.  Needs Python 3 on PATH, nothing else (no build).
rem It uses the two prebuilt TEST builds in testbuild\ (they are NOT the release; built with build\build.sh, NUNITS=32):
rem    zeval_ut.exe       -DUNITTEST         32 units; options Unit / Conc / ConcOnly; transposition table on
rem    zeval_ut_nott.exe  -DUNITTEST -DNOTT  the same with the transposition table off (searches stay deterministic when several run at once)
rem    zeval_ut_plain.exe -DUNITTEST -DTTPLAIN  the former non-lock-free table scheme (only for the comparison in step 6: it may show corrupt entries, the release never has it)
rem and the release zeval.exe.  What it exercises: CreateThread / WaitForSingleObject of the unit hook
rem (os_thread_run, conc_spawn, conc_join in src\p7_units.asm) and 32 searches running at the same time.
rem usage:  verify_units.bat            (to keep the output:  verify_units.bat > verify_units_result.txt 2>&1)
setlocal
cd /d "%~dp0"
set "UT=%~dp0testbuild\zeval_ut.exe"
set "NOTT=%~dp0testbuild\zeval_ut_nott.exe"
set "PLAIN=%~dp0testbuild\zeval_ut_plain.exe"
for %%f in ("%UT%" "%NOTT%" "%PLAIN%" "%~dp0zeval.exe") do if not exist %%f (echo ERROR: not found: %%f & exit /b 1)
echo === machine
ver
echo %PROCESSOR_IDENTIFIER%  logical processors: %NUMBER_OF_PROCESSORS%
set GOLDEN_OPTS=
echo === 1/7 release zeval.exe: golden (60 positions, depth 8)
python tests\t_golden.py "%~dp0zeval.exe" || exit /b 1
echo === 2/7 test build, unit 0 on the main thread: golden
python tests\t_golden.py "%UT%" || exit /b 1
echo === 3/7 test build, search in a helper thread of unit 1 (CreateThread, WaitForSingleObject): golden
set GOLDEN_OPTS=Unit=1
python tests\t_golden.py "%UT%" || exit /b 1
echo === 4/7 test build, helper thread of unit 31: golden
set GOLDEN_OPTS=Unit=31
python tests\t_golden.py "%UT%" || exit /b 1
set GOLDEN_OPTS=
echo === 5/7 concurrency: 5 and 32 units at the same time == each unit alone; 32 units with the shared table
python tests\t_units_conc.py --units 32 --exe "%NOTT%" --exe-tt "%UT%" || exit /b 1
echo === 6/7 lock-free transposition table: 8 threads hammer one table, corrupt entries must be 0; the old scheme is run for comparison (informational)
python tests\t_ttrace.py --exe "%UT%" --exe-plain "%PLAIN%" --units 8 --iters 5000 || exit /b 1
echo === 7/7 timing of the unit build (informational): release zeval.exe against itself, noise level of this machine
python tests\bench_ab.py "%~dp0zeval.exe" "%~dp0zeval.exe" --runs 9
echo.
echo ALL CHECKS PASSED
