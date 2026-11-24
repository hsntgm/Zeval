@echo off
rem ---------------------------------------------------------------------------------------------
rem Build zeval.exe (Windows x64) from source.
rem Run from an "x64 Native Tools Command Prompt for VS" (gives link.exe, rc.exe, lib.exe, editbin.exe) with
rem nasm.exe (https://www.nasm.us) on PATH.  Output: dist\zeval.exe
rem Same image as build.sh (lld-link): fixed base 0x400000, relocation table kept, large-address aware (the engine data uses
rem 32-bit absolute addresses inside the image).  MSVC link.exe refuses 32-bit absolute addresses in a large-address-aware
rem 64-bit image (LNK2017), so the exe is linked with /largeaddressaware:no and the flag is set afterwards with editbin.
rem ---------------------------------------------------------------------------------------------
setlocal
cd /d "%~dp0.."
where nasm >nul 2>nul || (echo ERROR: nasm.exe not found on PATH & exit /b 1)
where link >nul 2>nul || (echo ERROR: link.exe not found - open an "x64 Native Tools Command Prompt" & exit /b 1)
where editbin >nul 2>nul || (echo ERROR: editbin.exe not found - open an "x64 Native Tools Command Prompt" & exit /b 1)
if not exist dist mkdir dist
rem same part order as build.sh (p2b_bb_data.inc + p2b_bb.asm = magic bitboards, BB=1 default).  Optional: set NASMFLAGS=-DBB=0 for the pure 0x88 engine (0x88 move generator)
if "%NUNITS%"=="" set NUNITS=32
copy /b src\p1_data.asm+src\eval.asm+src\p2_core.asm+src\p2b_bb_data.inc+src\p2b_bb.asm+src\p3_cmd.asm+src\p4_search.asm+src\p5_main.asm+src\p6_win.asm+src\p7_units.asm zeval.asm >nul || exit /b 1
rem one object per thread unit: unit 0 = whole engine, units 1.. = search code + own state block
set OBJS=
set /a LAST=%NUNITS%-1
for /l %%k in (0,1,%LAST%) do (
  nasm -f win64 -DWIN -DUNIT=%%k -DNUNITS=%NUNITS% %NASMFLAGS% zeval.asm -o dist\u%%k.obj || exit /b 1
)
for /l %%k in (0,1,%LAST%) do call set OBJS=%%OBJS%% dist\u%%k.obj
lib /nologo /machine:x64 /def:build\kernel32_min.def /out:dist\kernel32_min.lib || exit /b 1
pushd build
rc /nologo /fo ..\dist\zeval.res zeval.rc
if errorlevel 1 (popd & exit /b 1)
popd
link /nologo /subsystem:console,6.00 /entry:win_entry /nodefaultlib /base:0x400000 /fixed:no /dynamicbase:no /highentropyva:no /largeaddressaware:no /ignore:4281 /stack:4194304,4194304 /brepro /out:dist\zeval.exe %OBJS% dist\zeval.res dist\kernel32_min.lib || exit /b 1
editbin /nologo /largeaddressaware dist\zeval.exe || exit /b 1
echo.
echo Built dist\zeval.exe
echo Verify with:  verify.bat dist\zeval.exe
