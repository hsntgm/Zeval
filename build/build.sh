#!/bin/sh
# Build zeval.exe (Windows x64) and the Linux ELF from a Linux box.
# needs: nasm, lld-link (LLVM lld), llvm-dlltool, llvm-rc   (apt install nasm lld llvm); llvm-rc runs with -no-preprocess (zeval.rc has no directives, no clang needed)
set -e
# default = magic bitboard build (src/p2b_bb.asm);  NASMFLAGS="-DBB=0" sh build/build.sh  -> the pure 0x88 engine (0x88 move generator)
# NUNITS = number of thread units (default 32 = the maximum number of search threads): the engine is assembled
# once per unit (nasm -DUNIT=k) and the objects are linked together; unit 0 is the whole engine, units 1.. only the search code and
# their own state block (src/p1_data.asm).  NUNITS=1 gives the single-thread engine.
#   NUNITS=2 NASMFLAGS="-DUNITTEST" sh build/build.sh   -> test build with the helper-unit hook (tests/t_units.py)
cd "$(dirname "$0")/.."
NUNITS=${NUNITS:-32}
cat src/p1_data.asm src/eval.asm src/p2_core.asm src/p2b_bb_data.inc src/p2b_bb.asm src/p3_cmd.asm src/p4_search.asm src/p5_main.asm src/p6_win.asm src/p7_units.asm > zeval.asm
mkdir -p dist
rm -f dist/u*.o dist/u*.obj
# Linux (p6 is skipped by %ifdef WIN)
LOBJ=""; WOBJ=""; k=0
while [ $k -lt $NUNITS ]; do
  nasm -f elf64 -DUNIT=$k -DNUNITS=$NUNITS $NASMFLAGS zeval.asm -o dist/u$k.o
  nasm -f win64 -DWIN -DUNIT=$k -DNUNITS=$NUNITS $NASMFLAGS zeval.asm -o dist/u$k.obj
  LOBJ="$LOBJ dist/u$k.o"; WOBJ="$WOBJ dist/u$k.obj"
  k=$((k+1))
done
ld $LOBJ -o dist/zeval
# Windows
llvm-dlltool -m i386:x86-64 -d build/kernel32_min.def -l dist/kernel32_min.lib
( cd build && llvm-rc -no-preprocess /FO ../dist/zeval.res zeval.rc )
lld-link /nologo /subsystem:console,6.00 /entry:win_entry /nodefaultlib \
    /base:0x400000 /fixed:no /dynamicbase:no /highentropyva:no /largeaddressaware \
    /stack:4194304,4194304 /brepro /out:dist/zeval.exe $WOBJ dist/zeval.res dist/kernel32_min.lib
rm -f dist/u*.o dist/u*.obj
ls -l dist/zeval dist/zeval.exe
