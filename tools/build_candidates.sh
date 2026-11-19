#!/bin/sh
# Rebuilds every candidate exe listed in candidates/FLAGS.txt from src/ (Linux, same toolchain as build/build.sh) into candidates/.
#
# usage:  sh tools/build_candidates.sh            (from anywhere; needs nasm, ld, lld-link, llvm-dlltool, llvm-rc = what build/build.sh needs)
#
# Behaviour:
#   - the first failed build stops the script with exit code 1 and the last lines of the build log (the log is kept);
#   - an existing dist/ (the folder build/build.sh writes to) is moved aside before the first build and put back at the end, also after an error or Ctrl+C;
#   - a candidate exe is replaced only after a successful build (copied via a temporary name);
#   - after every build the SHA-256 is compared with SHA256SUMS.txt (build/build.sh links with /brepro, so the same toolchain reproduces the shipped exe bit for bit:
#     "matches" = reproduced; "DIFFERS" = another toolchain version or other flags, not necessarily a defect);
#   - zeval.asm (which build/build.sh regenerates) is restored.
# Takes about 1-2 minutes per candidate (32 thread units, two assembler runs each).
ROOT=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd) || { echo "build_candidates.sh: cannot find the package folder" >&2; exit 1; }
cd "$ROOT" || exit 1
[ -f candidates/FLAGS.txt ] || { echo "build_candidates.sh: candidates/FLAGS.txt not found" >&2; exit 1; }
[ -f build/build.sh ] || { echo "build_candidates.sh: build/build.sh not found" >&2; exit 1; }
for t in nasm ld lld-link llvm-dlltool llvm-rc; do
  command -v "$t" >/dev/null 2>&1 || { echo "build_candidates.sh: '$t' not found (apt install nasm lld llvm)" >&2; exit 1; }
done

TMP=$(mktemp -d "${TMPDIR:-/tmp}/zeval_cand.XXXXXX") || exit 1
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
  mv dist "$SAVED" || { echo "build_candidates.sh: cannot move dist/ aside" >&2; SAVED=""; exit 1; }
  echo "(your dist/ was moved to $SAVED and will be put back at the end)"
fi

built=0; same=0; differ=0
while IFS=': ' read -r name flags; do
  case "$name" in ''|'#'*) continue ;; esac
  echo "building candidates/zeval_$name.exe  [$flags]"
  if ! NUNITS=32 NASMFLAGS="$flags" sh build/build.sh >"$LOG" 2>&1; then
    echo "build failed: $name   (last lines of the build log:)" >&2
    tail -n 15 "$LOG" >&2
    exit 1
  fi
  if [ ! -s dist/zeval.exe ]; then echo "build of $name ended without dist/zeval.exe" >&2; exit 1; fi
  cp dist/zeval.exe "candidates/.zeval_$name.exe.new" && mv -f "candidates/.zeval_$name.exe.new" "candidates/zeval_$name.exe" || { echo "cannot write candidates/zeval_$name.exe" >&2; exit 1; }
  built=$((built + 1))
  if command -v sha256sum >/dev/null 2>&1; then
    sum=$(sha256sum "candidates/zeval_$name.exe" | cut -d' ' -f1)
    want=$(awk -v f="candidates/zeval_$name.exe" '$2 == f { print $1 }' SHA256SUMS.txt 2>/dev/null)
    if [ -z "$want" ]; then echo "  built candidates/zeval_$name.exe  sha256 $(printf %.8s "$sum")...  (not listed in SHA256SUMS.txt)"
    elif [ "$sum" = "$want" ]; then echo "  built candidates/zeval_$name.exe  matches SHA256SUMS.txt"; same=$((same + 1))
    else echo "  built candidates/zeval_$name.exe  DIFFERS from SHA256SUMS.txt (another toolchain version or other flags)"; differ=$((differ + 1)); fi
  else
    echo "  built candidates/zeval_$name.exe"
  fi
done < candidates/FLAGS.txt
echo "done: $built candidate(s) built, $same identical to SHA256SUMS.txt, $differ different"
[ "$built" -gt 0 ] || { echo "build_candidates.sh: FLAGS.txt lists no candidate" >&2; exit 1; }
exit 0
