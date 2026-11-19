#!/bin/sh
# Static hygiene check of the scripts and data files of the package.  No engine is run, nothing is written unless --fix is given.   Seconds.
#
# usage:  sh tools/lint_scripts.sh [--fix]          (from anywhere; needs python3 and sh; bash only if a script has a bash shebang)
#
# Checks:
#   1. SYNTAX    every *.py of tests/ spike/ tools/ tactics/tools/ tuner/ parses (ast, no __pycache__ is written); every *.sh passes `sh -n` (`bash -n` for a bash shebang)
#   2. CRLF      no Python / shell script has Windows line ends (a CRLF shebang line "#!/usr/bin/env python3^M" makes `./script.py` fail on Linux).
#                *.bat files are the opposite: they MUST have CRLF (cmd.exe can mis-parse labels / goto in an LF-only batch file).
#   3. MODES     no file under tests/ spike/ tools/ tactics/ tuner/ candidates/ is unreadable for group / others (a file with mode 0600 cannot be read
#                after "unzip" as another user or in a shared checkout).
# --fix repairs 2 and 3 (CRLF -> LF in scripts, LF -> CRLF in .bat files, chmod go+r) and says what it changed; 1 can only be reported.
# Line ends and modes cannot be carried by a text patch (git knows only the modes 644 / 755), so this check is part of the packaging step:
#   sh tools/lint_scripts.sh --fix  (then run it once more: it must say CLEAN).
# Exit code 0 = clean (or everything was fixed), 1 = a problem remains.
FIX=0; [ "$1" = "--fix" ] && FIX=1
ROOT=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd) || { echo "lint_scripts.sh: cannot find the package folder" >&2; exit 1; }
cd "$ROOT" || exit 1
command -v python3 >/dev/null 2>&1 || { echo "lint_scripts.sh: python3 not found" >&2; exit 1; }
bad=0; fixed=0; n=0
CR=$(printf '\r')
files=$(ls tests/*.py spike/*.py tools/*.py tactics/tools/*.py tuner/*.py tests/*.sh spike/*.sh tools/*.sh tactics/tools/*.sh 2>/dev/null)
for f in $files; do
  n=$((n + 1))
  case "$f" in
    *.py) python3 -I -c 'import ast,sys; ast.parse(open(sys.argv[1], "rb").read(), sys.argv[1])' "$f" 2>&1 || { echo "SYNTAX  $f"; bad=1; } ;;
    *.sh) if head -n 1 "$f" | grep -q bash; then bash -n "$f" || { echo "SYNTAX  $f"; bad=1; }; else sh -n "$f" || { echo "SYNTAX  $f"; bad=1; }; fi ;;
  esac
  if grep -q "$CR" "$f"; then
    if [ $FIX = 1 ]; then sed -i "s/$CR\$//" "$f" && { echo "fixed   $f: CRLF -> LF"; fixed=$((fixed + 1)); }
    else echo "CRLF    $f (Windows line ends in a script)"; bad=1; fi
  fi
done
for b in $(ls *.bat build/*.bat spike/*.bat 2>/dev/null); do
  # a .bat file needs CRLF on every line: count the lines that do NOT end in CR
  if [ "$(grep -c -v "$CR\$" "$b")" -gt 0 ]; then
    if [ $FIX = 1 ]; then sed -i "s/$CR\$//; s/\$/$CR/" "$b" && { echo "fixed   $b: LF -> CRLF"; fixed=$((fixed + 1)); }
    else echo "LF      $b (a batch file needs CRLF line ends)"; bad=1; fi
  fi
done
for f in $(find tests spike tools tactics tuner candidates -type f ! -perm -044 ! -path '*/__pycache__/*' 2>/dev/null); do
  if [ $FIX = 1 ]; then chmod go+r "$f" && { echo "fixed   $f: mode now go+r"; fixed=$((fixed + 1)); }
  else echo "MODE    $f is not readable for group / others ($(stat -c %a "$f" 2>/dev/null))"; bad=1; fi
done
if [ $FIX = 1 ]; then echo "lint_scripts: $n scripts checked, $fixed fixed"; [ $bad = 0 ] && echo "CLEAN" || echo "PROBLEMS REMAIN (syntax errors cannot be fixed automatically)"
else [ $bad = 0 ] && echo "lint_scripts: $n scripts checked: CLEAN" || echo "lint_scripts: problems found (sh tools/lint_scripts.sh --fix repairs line ends and modes)"; fi
exit $bad
