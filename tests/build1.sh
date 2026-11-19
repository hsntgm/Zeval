#!/bin/bash
# usage: build1.sh OUT [nasm -D flags...]   (single-unit linux build)   env R = source dir (default: the package this script is in)
#   e.g.  sh tests/build1.sh /tmp/zeval_see -DSEETEST        sh tests/build1.sh /tmp/zeval_chk1 -DCHK_EXT=1
# One unit (-DUNIT=0 -DNUNITS=1) = the plain single-thread engine, quick to build (seconds); for test builds that need no helper threads.
# A missing argument or an unreadable src/ is an error message with exit code 1-2; the temp dir is removed also after an error.
[ $# -ge 1 ] || { echo "usage: build1.sh OUT [nasm -D flags...]" >&2; exit 2; }
R=${R:-$(cd "$(dirname "$0")/.." && pwd)}
out=$(realpath -m "$1"); shift
T=$(mktemp -d) || exit 1
trap 'rm -rf "$T"' EXIT
cd "$R/src" || { echo "build1.sh: cannot enter $R/src" >&2; exit 1; }
cat p1_data.asm eval.asm p2_core.asm p2b_bb_data.inc p2b_bb.asm p3_cmd.asm p4_search.asm p5_main.asm p6_win.asm p7_units.asm > "$T/x.asm" || { echo "build1.sh: a source part is missing in $R/src" >&2; exit 1; }
nasm -f elf64 -DUNIT=0 -DNUNITS=1 "$@" "$T/x.asm" -o "$T/x.o" && ld "$T/x.o" -o "$out"
