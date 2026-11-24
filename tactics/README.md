# Tactics suite

1,146 tactical test positions in 7 suites, with a runner, an analysis tool and recorded reference results. The suite
answers one question: does the engine still find tactical moves, and how fast? It does not measure playing strength.
A change can solve more positions and still lose games, and the reverse; only an SPRT (`tests/match.py`) decides
strength.

Everything is started through one script in the root of the checkout:

```sh
./tactics.sh <command>          # Linux, WSL, macOS
tactics.bat <command>           # Windows
```

Both scripts only find Python and call `tactics/tools/tactics.py`. Run either one without arguments for help. Nothing
in this folder changes the engine or is read by the engine or the other tests.

## Requirements

- Python 3.8 or newer.
- `python-chess` (`pip install chess`), needed for the analysis tables and the self-test.
- A built engine. The default is `zeval` (`zeval.exe`) in the root, then `dist/`. `-e ENGINE` or the environment
  variable `ENGINE` selects another one.
- For `variant`: Linux with `nasm` and `ld`.

## First checks

```sh
./tactics.sh check       # Python, python-chess, the engine answers UCI, suite files present: every line must say "ok"   (seconds)
./tactics.sh selftest    # must print SELFTEST PASSED                                                                   (about 20 s)
```

The self-test checks:

- the data files against `tactics/SHA256SUMS.txt`, and that every position and solution move is legal;
- that the suites rebuild bit for bit from the EPD files;
- that the analysis reproduces the recorded one exactly;
- a short engine run with resume, an A/B analysis, and the guard against mixing result files of different builds.

A failure means a damaged or missing file. It says nothing about the engine.

## Commands

| Command | What it runs | Time |
|---|---|---|
| `smoke` | wac + mates at 200 ms per position | about 1 min |
| `standard` | iq4, ecmgcp, sbd, classics, mates at 1000 ms, compared with the recorded baseline | about 11 min |
| `full` | all 7 suites at 1000 ms (the protocol of the recorded baseline) | about 19 min |
| `ab ENGINE_A ENGINE_B` | both engines, suite by suite in turn, then the analysis A - B (`--profile smoke\|standard\|full`, default standard) | twice the profile |
| `analyze TAG NAME_A NAME_B` | the analysis of two finished result files again, e.g. `analyze 1000ms NAME_A NAME_B` | seconds |
| `variant NAME -DFLAG=...` | builds `tactics/bin/zeval_NAME`: the default build plus the given flags (Linux) | seconds |
| `baseline` | prints the recorded reference analysis | |
| `list` | the suites and the finished result files | |

Options of `smoke`, `standard` and `full`:

| Option | Meaning |
|---|---|
| `-e ENGINE` | the engine to run |
| `-n NAME` | the name of the result |
| `-t MS` | time per position in milliseconds |
| `--suites a,b` | run only these suites |
| `--hash MB` | hash size (default 64) |
| `--fresh` | start the result file again |

Every position is searched from scratch (`ucinewgame`) with Threads 1 and Hash 64 MB. A position counts as solved when
the final best move is one of the `bm` moves, or, for avoid-move positions, none of the `am` moves.

The search runs on the clock, so a busy machine gives fewer nodes and fewer solved positions. Run nothing else heavy at
the same time.

## Result files

Each run writes to `tactics/results/<name>_<ms>ms.jsonl`. A `.meta.json` file next to it records which binary made it.
The default name is the binary's file name plus the first 6 hex digits of its SHA-256, for example `zeval-55bcb7`.

- **Resume.** Results are written position by position, so Ctrl+C is safe. Run the same command again and it continues
  where it stopped.
- **One binary per file.** A result file belongs to one binary. Running a different build under the same name is
  refused. Give it a new name with `-n`, or use `--fresh`; the old file is kept as `.bak`.
- **Reuse in `ab`.** `ab` also reuses finished result files. If one engine has already run the profile, only the other
  one runs. Use `--fresh` to run both again.

`tactics/results/` and `tactics/bin/` are ignored by git.

## When to use it

| Situation | Command |
|---|---|
| After any change to the search, evaluation, hash table or time management: a cheap tripwire against crashes, `ERROR` / `TIMEOUT` records or a collapsing suite | `smoke` before and after |
| After tuning the evaluation: did the new values cost tactics? | `ab zeval_tuned zeval_old` |
| A search idea: which kinds of moves did it gain or lose, and how fast are they found? | `variant NAME -D...`, then `ab tactics/bin/zeval_NAME zeval` |
| A new machine or a new build: is the binary sane here? | `tests/t_golden.py`, then `smoke` |

As a rule of thumb (not a measured threshold), a change deserves a closer look in either of these cases:

- a suite of 100 or more positions drops by more than about 5 percentage points against your own earlier run of the
  same profile on the same machine;
- a suite loses a third of its solved positions.

Any `ERROR` or `TIMEOUT` record also deserves a look. Run `ab` against the old binary before you blame the change.

Do not use the suite for these:

- **To decide whether a change is stronger.** A change accepted by an SPRT moved the hard solve rate by only +0.6
  percentage points, inside the noise.
- **To read small differences.** Differences below about 3 points on the hard set, or about 5 points on one suite, are
  inside the noise of a single run.
- **To tune parameters.** With 1,146 positions it overfits at once.
- **For equal-node comparisons.** The runner uses movetime only. Use `tests/t_epd.py --nodes` instead.

## Reading the output

A single run prints `solved / n` per suite. `standard` and `full` then compare with the recorded baseline. That
baseline was recorded on another machine, so use it only to see whether your numbers are in the same range.

The `ab` and `analyze` analysis contains:

| Part | Meaning |
|---|---|
| `A-B (a/b, p=..)` | e.g. `+3 (8/5, p=0.58)`: 8 positions only A solved, 5 only B solved, and an exact two-sided sign test on them. p above 0.05 means no detectable difference. |
| `HARD` | All suites except WAC, which is saturated. The solve-rate difference comes with a 95 % bootstrap interval; if it contains 0, nothing was shown. |
| SF16 subset | The hard positions that Stockfish 16 solved in the same time (1000 ms runs only). This leaves out suspect or very deep positions and is the fairest single number. |
| type / phase | Results by kind of solution move (capture, check, quiet, castle, avoid) and by phase (endgame = 12 men or fewer). |
| Time-to-solution | On positions both engines solve: the time after which every later best move was correct (median and mean). |
| depth / nps | Mean depth reached and mean speed. |

The search is timed, so two runs of the same binary differ by a few positions. For a decision, repeat the `ab`.

## Suites

| Suite | Positions | Content |
|---|---|---|
| `arasan2026` | 200 | Arasan test suite 2026: hard tactics and endgames (very hard at 1 s) |
| `iq4` | 183 | hard tactical middlegame positions |
| `ecmgcp` | 183 | Encyclopedia of Chess Middlegames, tactics |
| `sbd` | 134 | Silent But Deadly: quiet-move tactics |
| `classics` | 122 | LCT II, Lapuce2, Kaufman, Bratko-Kopec, Nolot, BT2630 |
| `mates` | 24 | hard mates (13 to 26 moves) |
| `wac` | 300 | Win At Chess, the same positions as `tests/wac.epd` |

The original EPD files are in `tactics/suites_epd/`. `tactics/suites/` holds the cleaned positions, one JSON object per
line (`suite`, `id`, `fen`, `bm`, `am`, moves in UCI notation). `_summary.json` lists the counts and the lines that were
dropped.

The collections are of mixed quality. Stockfish 16 does not solve 236 of the 846 hard positions in 1 s, and no
recorded engine solves 150 of them: some are suspect or simply too deep. Some positions may have more correct moves
than their `bm` list names.

## Recorded baseline

`tactics/reference/` holds the results of one `full` run (1000 ms per position, Threads 1, Hash 64, one core) for three
engines:

- two earlier builds of this engine, `C6` and `DEF`;
- Stockfish 16 (`SF16`) as a reference.

`./tactics.sh baseline` prints the analysis, which is also in `analysis_1000ms.txt`.

| Suite | n | C6 | DEF | SF16 |
|---|---|---|---|---|
| arasan2026 | 200 | 3.5 % | 3.0 % | 59.0 % |
| iq4 | 183 | 72.1 % | 71.6 % | 70.5 % |
| ecmgcp | 183 | 48.1 % | 48.6 % | 77.6 % |
| sbd | 134 | 80.6 % | 79.9 % | 79.9 % |
| classics | 122 | 51.6 % | 50.0 % | 77.9 % |
| mates | 24 | 62.5 % | 58.3 % | 79.2 % |
| wac | 300 | 96.7 % | 96.3 % | 91.7 % |
| HARD (no WAC) | 846 | 48.8 % | 48.2 % | 72.1 % |

Absolute rates depend on the machine. Always compare two builds on the same machine with `ab`.

## Building variants

```sh
./tactics.sh variant NOSEEPC -DSEE_PC=0
./tactics.sh ab tactics/bin/zeval_NOSEEPC zeval
```

The build runs in a temporary folder; `dist/`, `zeval.asm` and the release binaries are not touched. A variant without
flags is bit-identical to the default build. The search switches are defined in `src/p1_data.asm` and the evaluation switches in
`src/eval.asm`; `candidates/FLAGS.txt` has examples.

On Windows, build with `set NASMFLAGS=-D...` and `build\build.bat`, copy `dist\zeval.exe` under a new name, and pass it
to `ab`.

## Other tactical tests

| Script | Use |
|---|---|
| `tests/t_tactics.py` | a few hand-made positions in seconds: the first thing to run |
| `tests/t_epd.py` | any EPD file, A/B of builds, by movetime or by node count |
| `tactics/` | the broad, reproducible tactical profile with statistics and a recorded baseline |

## Adding a suite

1. Put the EPD file into `tactics/suites_epd/` and add it to `SUITES` in `tools/build_suites.py`.
2. Run `python3 tactics/tools/build_suites.py`. Illegal positions and unreadable moves are dropped and listed.
3. Add the name to `ALLSUITES` / `PROFILES` in `tools/tactics.py` and to `ORDER` in `tools/analyze.py`.
4. Regenerate the checksums:
   `cd tactics && find suites suites_epd reference -type f | sort | xargs sha256sum > SHA256SUMS.txt`
5. Update the expected position count in `tools/selftest.py` and run `./tactics.sh selftest`.

A changed suite cannot be compared with older result files, so use new result names.

## Layout

| Path | Content |
|---|---|
| `tools/tactics.py` | the front end for all commands |
| `tools/run_suite.py` | the UCI runner: one position at a time, resumable, records time-to-solution |
| `tools/analyze.py` | the analysis |
| `tools/selftest.py` | the self-test |
| `tools/build_suites.py` | EPD to JSON lines: cleaning and normalising, reproducible |
| `tools/build_variant.sh` | engine variants (Linux) |
| `suites/`, `suites_epd/` | the positions, cleaned and as downloaded |
| `reference/` | the recorded results and their analysis |
| `results/`, `bin/` | your runs and your variants (not in git) |
| `SHA256SUMS.txt` | checksums of `suites/`, `suites_epd/` and `reference/` |

The EPD files come from the Arasan test suites (Jon Dart) and the Chess-EPDs collection of Chris Whittington.
