@echo off
rem Runs the SPRT of every ablation build against zeval.exe (the default build) one after the other (the ablation set of candidates\FLAGS.txt; A0_DEV8 is not in the list: run it by hand).  Extra arguments are passed on: sprt_all.bat 10+0.1 10
setlocal
cd /d "%~dp0"
for %%N in (A1_noTM A2_noPRUNE A3_noLMR A4_noEVALNMP A5_IIR E0_PSTEVAL) do (
  call sprt_candidate.bat %%N %*
)
