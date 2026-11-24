#!/bin/sh
# Builds and packages a release (Linux, the toolchain of build/build.sh).  Used by .github/workflows/release.yml; runs the same way by hand.
#
# usage:  sh tools/package_release.sh VERSION [OUTDIR]          e.g.  sh tools/package_release.sh 1.0          (OUTDIR default: release/)
#
# Writes into OUTDIR:
#   zeval-VERSION-windows-x64.zip       zeval-VERSION/zeval.exe + README.md, LICENSE, CREDITS.md
#   zeval-VERSION-linux-x64.tar.gz      zeval-VERSION/zeval     + README.md, LICENSE, CREDITS.md
#   zeval-VERSION-testkit-windows.zip   testbuild/*.exe and candidates/*.exe: unpacked into the root of a checkout of the same tag, they are
#                                       the prebuilt test builds that verify_smp.bat, verify_units.bat and sprt_candidate.bat use
#   SHA256SUMS.txt                      the archives, and the two engine binaries inside them
# The version must match the engine's identity (tools/check_version.sh).  Takes about 15-30 minutes (one release build, four test builds,
# one build per line of candidates/FLAGS.txt; 32 thread units each).
set -e
VER=$1
[ -n "$VER" ] || { echo "usage: sh tools/package_release.sh VERSION [OUTDIR]" >&2; exit 2; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
OUT=$(mkdir -p "${2:-release}" && cd "${2:-release}" && pwd)
for t in nasm ld lld-link llvm-dlltool llvm-rc zip tar sha256sum; do
  command -v "$t" >/dev/null 2>&1 || { echo "package_release.sh: '$t' not found (apt install nasm lld llvm zip)" >&2; exit 1; }
done
sh tools/check_version.sh "$VER"

STAGE=$(mktemp -d "${TMPDIR:-/tmp}/zeval_release.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
NAME="zeval-$VER"
mkdir -p "$STAGE/win/$NAME" "$STAGE/lin/$NAME" "$STAGE/kit/testbuild" "$STAGE/kit/candidates"

echo "== release build"
rm -rf dist
sh build/build.sh > "$STAGE/build.log" 2>&1 || { tail -20 "$STAGE/build.log"; exit 1; }
cp dist/zeval "$STAGE/lin/$NAME/zeval"
cp dist/zeval.exe "$STAGE/win/$NAME/zeval.exe"
chmod 755 "$STAGE/lin/$NAME/zeval"
for f in README.md LICENSE CREDITS.md; do
  [ -f "$f" ] || { echo "package_release.sh: $f not found" >&2; exit 1; }
  cp "$f" "$STAGE/win/$NAME/"; cp "$f" "$STAGE/lin/$NAME/"
done

echo "== test builds (testbuild/)"
tb() { n=$1; shift; rm -rf dist; NASMFLAGS="$*" sh build/build.sh > "$STAGE/tb_$n.log" 2>&1 || { tail -20 "$STAGE/tb_$n.log"; exit 1; }; cp dist/zeval.exe "$STAGE/kit/testbuild/$n.exe"; }
tb zeval_smptest -DSMPTEST -DNOTT -DSMP_ASPSTEP=0
tb zeval_ut -DUNITTEST
tb zeval_ut_nott -DUNITTEST -DNOTT
tb zeval_ut_plain -DUNITTEST -DTTPLAIN
rm -rf dist

echo "== candidate builds (candidates/)"
sh tools/build_candidates.sh > "$STAGE/cand.log" 2>&1 || { tail -20 "$STAGE/cand.log"; exit 1; }
cp candidates/zeval_*.exe "$STAGE/kit/candidates/"
n=$(ls "$STAGE/kit/candidates" | wc -l)
want=$(grep -cE '^[A-Z][A-Za-z0-9_]*:' candidates/FLAGS.txt)
[ "$n" -eq "$want" ] || { echo "package_release.sh: $n candidate exes, $want lines in candidates/FLAGS.txt" >&2; exit 1; }

echo "== archives"
rm -f "$OUT/$NAME"-*.zip "$OUT/$NAME"-*.tar.gz "$OUT/SHA256SUMS.txt"
(cd "$STAGE/win" && zip -qr -X "$OUT/$NAME-windows-x64.zip" "$NAME")
(cd "$STAGE/lin" && tar --owner=0 --group=0 --numeric-owner -czf "$OUT/$NAME-linux-x64.tar.gz" "$NAME")
(cd "$STAGE/kit" && zip -qr -X "$OUT/$NAME-testkit-windows.zip" testbuild candidates)
(cd "$OUT" && sha256sum "$NAME-windows-x64.zip" "$NAME-linux-x64.tar.gz" "$NAME-testkit-windows.zip" > SHA256SUMS.txt)
(cd "$STAGE/win/$NAME" && sha256sum zeval.exe) >> "$OUT/SHA256SUMS.txt"
(cd "$STAGE/lin/$NAME" && sha256sum zeval) >> "$OUT/SHA256SUMS.txt"
cat "$OUT/SHA256SUMS.txt"
ls -l "$OUT"
