#!/bin/sh
# Builds the Windows TEST builds that verify_smp.bat and verify_units.bat use into testbuild/ (Linux, same toolchain as build/build.sh).
# They are not the release engine.  A release package has them in its test kit; a checkout builds them with this script
# (Windows: tools\build_testbuilds.bat).
#   zeval_smptest.exe   -DSMPTEST -DNOTT -DSMP_ASPSTEP=0     verify_smp.bat (tests/t_smp_equiv.py --exe)
#   zeval_ut.exe        -DUNITTEST                           verify_units.bat
#   zeval_ut_nott.exe   -DUNITTEST -DNOTT                    verify_units.bat
#   zeval_ut_plain.exe  -DUNITTEST -DTTPLAIN                 verify_units.bat (comparison only)
# usage:  sh tools/build_testbuilds.sh        (from anywhere; needs nasm, ld, lld-link, llvm-dlltool, llvm-rc)
# An existing dist/ is moved aside and put back at the end (also after an error or Ctrl+C); zeval.asm is restored.  About 1-2 minutes per build.
ROOT=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd) || { echo "build_testbuilds.sh: cannot find the checkout" >&2; exit 1; }
cd "$ROOT" || exit 1
[ -f build/build.sh ] || { echo "build_testbuilds.sh: build/build.sh not found" >&2; exit 1; }
for t in nasm ld lld-link llvm-dlltool llvm-rc; do
  command -v "$t" >/dev/null 2>&1 || { echo "build_testbuilds.sh: '$t' not found (apt install nasm lld llvm)" >&2; exit 1; }
done

TMP=$(mktemp -d "${TMPDIR:-/tmp}/zeval_tb.XXXXXX") || exit 1
LOG="$TMP/build.log"
SAVED=""
[ -f zeval.asm ] && cp zeval.asm "$TMP/zeval.asm.orig"
cleanup() {
  rm -rf dist
  [ -n "$SAVED" ] && [ -e "$SAVED" ] && mv "$SAVED" dist
  [ -f "$TMP/zeval.asm.orig" ] && cp "$TMP/zeval.asm.orig" zeval.asm
  rm -rf "$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP
if [ -e dist ]; then
  SAVED="dist.saved.$$"
  mv dist "$SAVED" || { echo "build_testbuilds.sh: cannot move dist/ aside" >&2; SAVED=""; exit 1; }
fi
mkdir -p testbuild || exit 1

build() {
  name=$1; shift
  echo "building testbuild/$name.exe  [$*]"
  if ! NUNITS=32 NASMFLAGS="$*" sh build/build.sh >"$LOG" 2>&1; then
    echo "build failed: $name   (last lines of the build log:)" >&2
    tail -n 15 "$LOG" >&2
    exit 1
  fi
  [ -s dist/zeval.exe ] || { echo "build of $name ended without dist/zeval.exe" >&2; exit 1; }
  cp dist/zeval.exe "testbuild/.$name.exe.new" && mv -f "testbuild/.$name.exe.new" "testbuild/$name.exe" || { echo "cannot write testbuild/$name.exe" >&2; exit 1; }
}
build zeval_smptest -DSMPTEST -DNOTT -DSMP_ASPSTEP=0
build zeval_ut -DUNITTEST
build zeval_ut_nott -DUNITTEST -DNOTT
build zeval_ut_plain -DUNITTEST -DTTPLAIN
echo "done: testbuild/zeval_smptest.exe zeval_ut.exe zeval_ut_nott.exe zeval_ut_plain.exe"
exit 0
