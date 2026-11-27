# Tools

Helpers for building, releasing, maintaining and tuning Zeval. None of them is needed to build or use the engine.
The test scripts are in [`tests/`](../tests/README.md) and the evaluation tuner is in [`tuner/`](../tuner/README.md).

| Tool | Purpose | Needs |
|---|---|---|
| `build_testbuilds.sh` / `.bat` | Builds the Windows test builds used by `verify_smp.bat` and `verify_units.bat`. | the build toolchain |
| `build_candidates.sh` | Rebuilds the candidate builds listed in `candidates/FLAGS.txt`. | the build toolchain |
| `package_release.sh` | Builds and packages a release. | the build toolchain, `zip` |
| `check_version.sh` | Checks that every place carrying the version number agrees. | `sh` |
| `lint_scripts.sh` | Syntax, line endings and file modes of all scripts. | `python3`, `sh` |
| `gen_magics.c` | Generates the magic bitboard tables (`src/p2b_bb_data.inc`). | a C compiler |
| `gen_kpk.py` | Generates or checks the king + pawn vs king bitbase in `src/eval.asm`. | `python3` |
| `spsa.py` | SPSA tuning of the search and evaluation constants of a `-DTUNE` build. | `python3`, `python-chess` |
| `tb_calibrate.py` | Measures the static evaluation against Syzygy tablebase verdicts, class by class. | `python3`, `python-chess`, Syzygy WDL files |

"The build toolchain" is what `build/build.sh` needs: Linux with `nasm`, `ld`, `lld-link`, `llvm-dlltool` and `llvm-rc`.
The shell scripts can be started from any folder. Run the Python tools and `gen_magics` from the root of the checkout.

## Building

### build_testbuilds.sh / build_testbuilds.bat

```sh
sh tools/build_testbuilds.sh          # Linux
tools\build_testbuilds.bat            # Windows, from an "x64 Native Tools Command Prompt" with nasm on PATH
```

Builds four instrumented Windows engines into `testbuild/`:

| File | Flags | Used by |
|---|---|---|
| `zeval_smptest.exe` | `-DSMPTEST -DNOTT -DSMP_ASPSTEP=0` | `verify_smp.bat` |
| `zeval_ut.exe` | `-DUNITTEST` | `verify_units.bat` |
| `zeval_ut_nott.exe` | `-DUNITTEST -DNOTT` | `verify_units.bat` |
| `zeval_ut_plain.exe` | `-DUNITTEST -DTTPLAIN` | `verify_units.bat` (comparison only) |

These are test builds, not the release engine. An existing `dist/` is moved aside and put back at the end, also after an
error or Ctrl+C.

### build_candidates.sh

```sh
sh tools/build_candidates.sh
```

Rebuilds every candidate listed in `candidates/FLAGS.txt` as `candidates/zeval_<NAME>.exe`. Each line has the form
`NAME: -D...`: the default build with one change. These builds are the input of `sprt_candidate.bat` and
`sprt_all.bat`.

- Each build takes up to a couple of minutes.
- The first failed build stops the script with exit code 1 and shows the end of the build log.
- A candidate is replaced only after its build succeeded.
- `dist/` and `zeval.asm` are restored at the end.
- When the `SHA256SUMS.txt` of a release is in the root of the checkout, every build is compared with it. The build is reproducible, so the
  same toolchain gives the same file. A difference usually means another toolchain version, not a defect.

To add a candidate, add a line to `candidates/FLAGS.txt` and run the script.

## Releasing

### check_version.sh

```sh
sh tools/check_version.sh 1.1
```

Checks that the UCI `id name`, the console banner, the Windows version resource (`build/zeval.rc`) and the manifest all
carry the given `MAJOR.MINOR` version.

- Every place that differs is listed as `WRONG`.
- Exit code: 0 when everything agrees, 1 when a place differs, 2 for wrong usage.

### package_release.sh

```sh
sh tools/package_release.sh 1.1 [OUTDIR]          # OUTDIR default: release/
```

| Output | Content |
|---|---|
| `zeval-VERSION-windows-x64.zip` | `zeval.exe`, README, LICENSE, CREDITS |
| `zeval-VERSION-linux-x64.tar.gz` | `zeval`, README, LICENSE, CREDITS |
| `zeval-VERSION-testkit-windows.zip` | the test builds and candidate builds, to be unpacked into a checkout of the same tag |
| `SHA256SUMS.txt` | checksums of the archives and of both engine binaries |

- The version must match the engine's identity: the script runs `check_version.sh` first.
- A full run builds the release engine, the four test builds and every candidate. It takes about 15 to 30 minutes.
- The release workflow (`.github/workflows/release.yml`) runs `check_version.sh`, `lint_scripts.sh` and this script when a
  version tag is pushed.

### lint_scripts.sh

```sh
sh tools/lint_scripts.sh            # report
sh tools/lint_scripts.sh --fix      # repair line endings and file modes, then run it again: it must print CLEAN
```

A static check that runs no engine. It checks three things:

- **Syntax.** Every Python script must parse, and every shell script must pass `sh -n`.
- **Line endings.** Python and shell scripts must use LF. `.bat` files must use CRLF, because cmd.exe can misread labels
  in a batch file with LF endings.
- **File modes.** Every file under `tests/`, `tools/`, `tactics/`, `tuner/` and `candidates/` must be readable by
  everyone.

`--fix` repairs line endings and file modes. Syntax errors are only reported.

- Exit code 0 means clean, 1 means a problem remains.
- Line endings and file modes do not survive a text patch, so run the check before packaging. CI also runs it on every
  push.

## Generated tables

### gen_magics.c

```sh
cc -O2 -o gen_magics tools/gen_magics.c
./gen_magics | cmp - src/p2b_bb_data.inc          # check: no output = identical
./gen_magics > src/p2b_bb_data.inc                # regenerate
```

Generates the fancy magic bitboard tables of the bitboard build: per square a mask, a magic number, an offset into one
shared attack table and a shift. The search for magics uses a fixed seed, so the output is byte for byte the shipped
file. GCC or Clang is required. On Windows, stdout is switched to binary mode so that `>` does not write CRLF.

Run it only when the magic scheme or the record layout changes, or to check the shipped file.

### gen_kpk.py

```sh
python3 tools/gen_kpk.py --check       # compare with src/eval.asm: exit code 0 = identical
python3 tools/gen_kpk.py               # print the table (the lines between kpk_bb: and kpk_bb_end: in src/eval.asm)
```

Builds the king + pawn vs king bitbase by retrograde analysis: one bit per position, set when the pawn side wins. The
endgame evaluation uses it. The index layout is described at the top of the script.

## Tuning and calibration

### spsa.py

SPSA tuning of the numeric `T<n>` options of a `-DTUNE` build. These options cover:

- the search margins,
- the evaluation constants,
- the endgame scale constants,
- the history and move ordering constants,
- the time curve.

The full list is `tune_def` in `src/p5_main.asm`. The piece-square tables and evaluation weights are tuned with the
[tuner](../tuner/README.md), not with SPSA.

```sh
sh tests/build1.sh zeval_tune -DTUNE                         # a single-thread -DTUNE build (Linux)
python3 tools/spsa.py zeval_tune --param T0=85,8,20,200 --param T4=110,10 \
        --iters 750 --pairs 2 --tc 10+0.1 --workers 8 --out spsa.json
```

- **`--param Tn=start,c[,min,max]`** gives the start value, the perturbation size and optional bounds. A `c` of about
  5 to 10 % of the value is a good start. A `c` too small to change the rounded integer is refused.
- **Start-up check.** Before any games, the script verifies that the build uses every parameter: a `-DTUNE` build declares
  as options `T<n>` only the slots its code uses, so a parameter whose switch is off is refused. A depth-9 search of four positions
  is then run with `start - c` and `start + c`; when it does not change, the script prints a note and goes on, because many
  parameters act only in some positions (endgames, rare thresholds) or in deeper searches. `--no-check` skips the check.
- **Each iteration** plays `--pairs` game pairs between two perturbed engines, using the openings and adjudication of
  `tests/match.py`. `--book`, `--epd` and `--no-adj` work as in `match.py`.
- **Which mode to use:**
  - Use `--no-adj` for evaluation constants, so that adjudication by the engine's own score does not bias them.
  - Use `--tc` for time-management parameters; they are refused with `--movetime`.
- **Results** are saved to `--out` every 10 iterations. `--resume` continues a stopped run, and a resumed run is the
  same run.
- **The result** includes the mean over the last iterations (`--avg-last`), which is less noisy than the final point.

Give SPSA few parameters, each with a clear effect. A few thousand games only move them coarsely; real runs use tens
of thousands. Verify every result with an SPRT, for example
`python3 tests/match.py zeval_tune zeval_tune --optA "T0=84;T4=112"`.

### tb_calibrate.py

```sh
python3 tools/tb_calibrate.py --tb /path/to/syzygy --engine dist/zeval [--engine other_build] \
        [--classes KRvKB,KRvKN,KRPvKR] [--n 400] [--seed 1] [--nodes 0]
```

Draws random quiet positions of each material class and labels them with the Syzygy WDL tables. Cursed wins and blessed
losses count as draws, because the engine plays under the fifty-move rule. For each class it reports how well the static
evaluation separates decisive from drawn positions:

- the AUC,
- the share of decisive positions with the right sign,
- the |eval| of drawn positions.

`--nodes N` adds a shallow search score. The same seed gives the same positions for every engine, so two builds can be
compared side by side. `--dry-run` runs without tablebases and prints only the engine's score distribution.

The positions are random, not taken from games. Read the result as "does the evaluation understand this class", not as
a strength figure. The files needed (3 to 5 pieces) take about 1 GB.
