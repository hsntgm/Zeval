#!/bin/sh
# Checks that every place that carries the engine version says VERSION (MAJOR.MINOR, e.g. 1.0 or 1.1):
#   src/p1_data.asm   "id name Zeval VERSION" (UCI) and the console banner "Zeval VERSION by ..."
#   build/zeval.rc    FILEVERSION / PRODUCTVERSION MAJOR,MINOR,0,0 and the strings "MAJOR.MINOR.0.0" / "VERSION"
#   build/zeval.manifest  version="MAJOR.MINOR.0.0"
# usage:  sh tools/check_version.sh VERSION        exit code 0 = all agree, 1 = a place differs (each one is listed), 2 = usage
VER=$1
case "$VER" in
  [0-9]*.[0-9]*) ;;
  *) echo "usage: sh tools/check_version.sh MAJOR.MINOR   (e.g. 1.1)" >&2; exit 2 ;;
esac
MAJ=${VER%%.*}; MIN=${VER#*.}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || exit 2
bad=0
need() {  # need FILE FIXED-STRING WHAT
  if grep -qF -- "$2" "$1"; then echo "ok    $1: $3"; else echo "WRONG $1: $3 (expected: $2)"; bad=1; fi
}
need src/p1_data.asm "\"id name Zeval $VER\"" "UCI id name"
need src/p1_data.asm "\"Zeval $VER by " "console banner"
need build/zeval.rc "FILEVERSION $MAJ,$MIN,0,0" "FILEVERSION"
need build/zeval.rc "PRODUCTVERSION $MAJ,$MIN,0,0" "PRODUCTVERSION"
need build/zeval.rc "\"FileVersion\", \"$MAJ.$MIN.0.0\"" "FileVersion string"
need build/zeval.rc "\"ProductVersion\", \"$VER\"" "ProductVersion string"
need build/zeval.rc "\"FileDescription\", \"Zeval $VER " "FileDescription"
need build/zeval.manifest "version=\"$MAJ.$MIN.0.0\"" "manifest version"
[ $bad = 0 ] && echo "version $VER: every place agrees" || { echo "version $VER: update the places marked WRONG"; exit 1; }
