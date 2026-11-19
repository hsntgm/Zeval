@echo off
rem Pentanomial SPRT of a candidate build against the default build (zeval.exe): Threads=1, paired openings (colours swapped), real clock.
rem usage:  sprt_candidate.bat NAME [TC] [WORKERS] [MAXGAMES] [ELO0] [ELO1]
rem   NAME     a name of candidates\FLAGS.txt, e.g. X12_TMOLD      (the file candidates\zeval_NAME.exe, built by tools/build_candidates.sh from candidates\FLAGS.txt)
rem   TC       default 10+0.1   (seconds + increment; the time management acts only on a real clock: never use --movetime for a TM candidate)
rem   WORKERS  default 10       (games in parallel; one engine searches at a time per game, so logical cores - 2 is fine on a quiet machine)
rem   MAXGAMES default 20000    ELO0 / ELO1 default 0 / 4  (H0: the candidate is not better, H1: it is at least +4 Elo; alpha = beta = 0.05)
rem The opening seed is drawn fresh and printed; the games are appended to results\NAME.jsonl.  Stop the run with Ctrl+C: the last printed line has the result so far.
setlocal
cd /d "%~dp0"
set "NAME=%~1"
if "%NAME%"=="" (echo usage: sprt_candidate.bat NAME [TC] [WORKERS] [MAXGAMES] [ELO0] [ELO1] & exit /b 1)
set "TC=%~2"
if "%TC%"=="" set "TC=10+0.1"
set "W=%~3"
if "%W%"=="" set "W=10"
set "G=%~4"
if "%G%"=="" set "G=20000"
set "E0=%~5"
if "%E0%"=="" set "E0=0"
set "E1=%~6"
if "%E1%"=="" set "E1=4"
if not exist "candidates\zeval_%NAME%.exe" (echo ERROR: candidates\zeval_%NAME%.exe not found & exit /b 1)
if not exist zeval.exe (echo ERROR: zeval.exe not found & exit /b 1)
if not exist results mkdir results
echo SPRT %NAME% vs zeval.exe : tc %TC%, %W% workers, max %G% games, H0 %E0% / H1 %E1% Elo
python tests\match.py "candidates\zeval_%NAME%.exe" zeval.exe --tc %TC% --workers %W% --games %G% --elo0 %E0% --elo1 %E1% --seed 0 --save-games "results\%NAME%.jsonl"
