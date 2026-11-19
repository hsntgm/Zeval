#!/bin/sh
# Zeval tactics suite - easy start (Linux / WSL / macOS).  Windows: tactics.bat.   Help: ./tactics.sh with no argument
#
#   ./tactics.sh check                       environment + engine + suite files                     (10 s)
#   ./tactics.sh selftest                    wiring regression test                                 (~20 s)
#   ./tactics.sh smoke                       wac + mates @ 200 ms                                   (~1 min)    tripwire after any code change
#   ./tactics.sh standard                    5 discriminating suites @ 1000 ms                      (~11 min)
#   ./tactics.sh full                        all 7 suites @ 1000 ms (protocol of the recorded base) (~19 min)
#   ./tactics.sh ab ENGINE_A ENGINE_B        alternating A/B on this machine + analysis             [--profile smoke|standard|full]
#   ./tactics.sh variant NAME -DFLAG=..       build tactics/bin/zeval_NAME (default + extra nasm flags)
#   ./tactics.sh baseline | list | analyze TAG NAME_A NAME_B
# Options of smoke / standard / full:  -e ENGINE   -n NAME   -t MOVETIME_MS   --suites a,b   --hash MB   --fresh        (./tactics.sh standard --help)
# Default engine = ./zeval next to this file (env ENGINE overrides).  Results: tactics/results/  (resumable: run the same command again).
cd "$(dirname "$0")" || exit 1
for P in python3 python; do
  if command -v "$P" >/dev/null 2>&1 && "$P" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    case "${1:-}" in
      ""|-h|--help|help) "$P" tactics/tools/tactics.py --help; exit $? ;;
    esac
    exec "$P" tactics/tools/tactics.py "$@"
  fi
done
echo "tactics.sh: python 3.8+ not found (apt install python3 python3-pip; pip install chess)" >&2
exit 1
