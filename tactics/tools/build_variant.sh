#!/bin/sh
# Build a Linux engine VARIANT (default build + extra nasm flags) for tactics A/B runs, WITHOUT touching zeval.asm, dist/ or the shipped binaries.
#
# usage:  sh tactics/tools/build_variant.sh NAME ["extra nasm flags"]        ->  tactics/bin/zeval_NAME
#   examples:   build_variant.sh NOSEEPC "-DSEE_PC=0"           (example: SEE capture prune off)
#               build_variant.sh DEV8    "-DDEV8SEARCH"          (the reference search = the "DEF" engine of tactics/reference)
#               build_variant.sh MYTEST  ""                       (plain rebuild of the default: must be bit-identical to ./zeval)
# Flags: see candidates/FLAGS.txt and the switch descriptions in src/p1_data.asm (a flag only has to say what DIFFERS from the default).
# Same procedure as the Linux half of build/build.sh (NUNITS=32, one nasm run per thread unit, ld); needs nasm + ld.  The Windows build stays build/build.sh.
set -e
NAME=${1:?usage: build_variant.sh NAME [\"extra nasm flags\"]}
FLAGS=${2:-}
NUNITS=${NUNITS:-32}
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$ROOT/tactics/bin"
command -v nasm >/dev/null 2>&1 || { echo "nasm not found (apt install nasm)"; exit 1; }
command -v ld   >/dev/null 2>&1 || { echo "ld not found (apt install binutils)"; exit 1; }
mkdir -p "$OUT"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/zeval_variant.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
S="$ROOT/src"
cat "$S/p1_data.asm" "$S/eval.asm" "$S/p2_core.asm" "$S/p2b_bb_data.inc" "$S/p2b_bb.asm" "$S/p3_cmd.asm" "$S/p4_search.asm" "$S/p5_main.asm" "$S/p6_win.asm" "$S/p7_units.asm" > "$TMP/zeval.asm"
# run in the temp directory with the same relative names as build/build.sh (zeval.asm, uK.o): the object files carry the source name, so a plain rebuild is bit-identical to ./zeval
cd "$TMP"
LOBJ=""; k=0
while [ $k -lt "$NUNITS" ]; do
  nasm -f elf64 -DUNIT=$k -DNUNITS=$NUNITS $FLAGS zeval.asm -o u$k.o
  LOBJ="$LOBJ u$k.o"; k=$((k+1))
done
ld $LOBJ -o "$OUT/zeval_$NAME"
echo "built $OUT/zeval_$NAME   flags: ${FLAGS:-(default)}"
sha256sum "$OUT/zeval_$NAME" 2>/dev/null || shasum -a 256 "$OUT/zeval_$NAME"
