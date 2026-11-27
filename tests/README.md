# Tests

The scripts in this folder check the engine: exact search reproduction, move generation, the UCI protocol, time management, the
evaluation, Lazy SMP and the transposition table. They also include tools for matches, benchmarks and diagnostics.

## Setup

- **Python 3.8 or newer.** Most scripts need `python-chess` (`pip install chess`); `t_div.py` needs `numpy`; `t_smp.py` uses `psutil` on Windows.
- **The engine.** Build it first (`sh build/build.sh` or `build\build.bat`). Without an argument, the scripts use `zeval` (`zeval.exe` on Windows) in the
  current folder, then in the root of the checkout, then in `dist/`. Set `ENGINE` to test another build:
  `ENGINE=/path/to/zeval python3 tests/t_uci.py`. `ENGINE` may also be a command line such as `"wine zeval.exe"`.
- **Test builds.** Scripts marked *build* compile their own instrumented engine in a temporary folder. They need Linux, `nasm` and `ld`
  (some also `nm` and `objdump`). The release binaries are never touched.
- **Exit codes.** `0` = passed, `1` = a check failed, `2` = the engine or a tool could not be run. Scripts marked *report* print numbers and
  have no pass/fail threshold.

## Quick check after a change

```sh
python3 tests/t_golden.py        # fixed-depth search identical to the reference
python3 tests/t_perft.py         # move generation
python3 tests/t_uci.py           # UCI protocol
python3 tests/t_robust.py        # command queueing, long input, malformed FEN
python3 tests/t_pv.py            # info lines, PV, bestmove / ponder
python3 tests/t_timecontrol.py   # every kind of go returns
```

On Windows, `verify.bat` runs the core checks (search reference, robustness, info lines, time controls, a short clock game). `verify_smp.bat` and `verify_units.bat` also need the test builds made
by `tools\build_testbuilds.bat` (Linux: `sh tools/build_testbuilds.sh`).

## Search and move generation

| Script | What it checks |
|---|---|
| `t_golden.py` | 60 positions at depth 8: score, nodes and best move of every iteration must equal a recorded reference (`golden_depth8*.json`, one per build variant, listed in the script). |
| `t_identical.py` | Two builds (e.g. Linux and Windows) give identical search output on the same positions. |
| `t_perft.py` | Move generation against python-chess (divide, 400 positions), plus the published perft totals of the six standard positions. |
| `t_ttage.py` | 130 identical searches in one process give identical trees (regression test for the table age counter). |
| `t_phash.py` | *build* The pawn cache and attack-map variants of the evaluation search the same trees as the default. |

## UCI protocol and robustness

| Script | What it checks |
|---|---|
| `t_uci.py` | 78 protocol checks: handshake, options, whitespace, `position` variants, every `go` limit, `searchmoves`, stop / quit / EOF, Hash, Clear Hash. |
| `t_pv.py` | Format and range of every info line, legality of the whole PV, bestmove = PV[0], ponder = PV[1], mate distances, currmove, hashfull. |
| `t_robust.py` | Commands queued behind a running search, `go infinite` waits for `stop`, a 1160-ply move list, console-mode repetition draw, malformed FEN. |
| `t_memsafety.py` | Game history beyond the undo stack, an absurd perft depth, malformed en-passant fields and 300 malformed FEN strings: no crash. |
| `t_stress.py` | 200 random positions with short searches: a legal bestmove, the position unchanged afterwards, latency. |
| `t_tactics.py` | 15 small tactical positions (mates, also on the 100th half-move; hanging pieces, promotion) at 600 ms: a quick tripwire. |

## Time management

| Script | What it checks |
|---|---|
| `t_timecontrol.py` | 14 `go` variants (clocks from 5 s down to 0 and negative, movetime, depth, infinite): every one ends with a bestmove. |
| `t_tmclock.py` | Short games on a real clock: legal moves, no flag fall; prints time per move. Use it to compare time-management builds. |
| `t_tmcurve.py` | The soft and hard limits of 1500 clock situations against a Python model of the formula. Needs a `-DTMCDBG` build (see the script). |

## Evaluation

| Script | What it checks |
|---|---|
| `t_eval.py` | Colour symmetry, incremental vs. from-scratch evaluation, tempo. `--selftest` (*build*) proves that broken evaluations are caught. |
| `t_div.py` | The reciprocal division of the taper equals exact integer division for every possible numerator. |
| `t_endgames.py` | *report* Self-play from won and drawn basic endgames (KQK, KRK, KBNK, KPK, KBPK): conversions and holds per build. |

## Lazy SMP and thread units

| Script | What it checks |
|---|---|
| `t_smp.py` | The release engine with 1 to 32 threads: thread counts, no busy thread after bestmove, stop / quit / EOF, legal moves, node counts of the info lines. |
| `t_smp_equiv.py` | *build* A helper thread runs the same search as the main thread (nodes, score, move), also with game history. |
| `t_smp_vote.py` | *build* The best-thread vote chooses exactly what its written rules choose. |
| `t_smp_audit.py` | *build* Static audit of the helper code: it writes no shared variable except the transposition table. |
| `t_chshared.py` | *build* With `CH_SHARED`, helpers write into the main thread's continuation history and nowhere else. |
| `t_units.py` | *build* Every thread unit searches exactly like unit 0 (golden), plus symbol audit and memory layout. |
| `t_units_conc.py` | *build* 5 and 32 units searching at the same time give the same results as each unit alone. |

## Transposition table

| Script | What it checks |
|---|---|
| `t_ttrace.py` | *build* Threads hammering one table never read a torn entry (lock-free scheme); the old scheme is shown to fail. |
| `t_ttfuzz.py` | *build* With random moves in every table hit, no illegal move is ever played or announced as ponder. |
| `t_hugepage.py` | Linux: the table is 2 MB aligned and backed by huge pages; resizing leaks no memory. |

## Diagnostics with special builds

Each script names the build it needs (`sh tests/build1.sh OUT -D...`).

| Script | What it checks |
|---|---|
| `t_see.py` | Static exchange evaluation against an independent python-chess reference. |
| `t_ppverify.py` | The pre-make legality and check test against make / unmake; `--selftest` builds broken variants. |
| `t_singular.py` | Rules and counters of the singular-extension exclusion search. |
| `t_ttfirst.py` | `TTFIRST` alone (`-DSTAGED=0`, with `-DEV_SCMG=0`): eager build = `golden_depth8_nostaged.json`, `tt_pseudo` against `gen` (bit flips and random values), garbage TT moves, lazy build = `golden_depth8_ttfirst.json`; `--selftest` builds 12 broken validators. |
| `t_staged.py` | `STAGED` (the default): golden reproduced, every move of a staged node picked exactly once (`-DSTAGEVERIFY`), garbage killers and TT moves; `--selftest` builds 5 broken versions. |
| `t_seereuse.py` | `CAPIDX` / `QSSEE`: golden reproduced bit for bit (also with `STAGED` 0 and with `TTFIRST` alone), every capture that `score_moves` marks as losing is refused by `see_ge` too (`-DQSSEEVERIFY`); `--selftest` builds 2 broken marks. |
| `t_scmg.py` | `EV_SCMG` (the default): the model reproduces the engine with the switch (`check_eval.py --endgame`; `HWPOPCNT` 2 and 0), positions with full material keep their eval, `-DSFASTVERIFY` with the switch; `--selftest` builds 3 broken factors. |
| `t_evspeed.py` | `EV_SFAST` / `EV_KLBITS` / `EV_BRSIGN` (on by default): golden reproduced, the same eval as all three off on random, endgame-class and king-line positions (`HWPOPCNT` 2 and 0, other constants of scale rule 6), `ev_scale` agrees wherever the fast path takes 64 (`-DSFASTVERIFY`); `--selftest` builds 10 broken versions. |
| `t_ordering.py` | *report* Move-ordering statistics, optionally against a reference (`ordering_ref_*.json`) or a second build; `--gate` makes it a check. |
| `t_tune.py` | `-DTUNE`: every read of the option table is marked (`TUSE`), and `uci` declares the options `T<n>` the build uses, with their current values. |

## Matches and strength

| Script | Purpose |
|---|---|
| `match.py` | Self-play match between two builds: paired openings, pentanomial SPRT, optional game recording. |
| `sprt.py` | SPRT and Elo arithmetic used by `match.py`; `--selftest` checks the error rates. |
| `cand_matrix.py` | Every candidate build against the default on several opening seeds, with a pooled summary and LOS. |
| `t_epd.py` | *report* Solve rate on an EPD suite (`bm` / `am`), for one build or an A/B comparison. |
| `t_epd_play.py` | *report* Plays out positions with a known result (`c0 "win"` / `"draw"`); needs such a suite. |
| `t_vs_stockfish.py` | *report* Average centipawn loss against Stockfish on random positions. |
| `make_endgames.py` | Builds a set of contested endgame start positions for `match.py --epd`. |

## Benchmarks and helpers

| Script | Purpose |
|---|---|
| `bench.py` | *report* Nodes, time and nps of fixed-depth searches. |
| `bench_ab.py` | Alternating speed comparison of two builds; fails above a 3 % slowdown or when the work differs. |
| `smp_scale.py` | *report* nps and time to depth for several thread counts. |
| `smoke_switches.py` | Legal moves and a clean exit for a build with some switch changed. |
| `threads_proxy.py` | Runs another test script with several search threads. |
| `build1.sh` | Quick single-thread Linux build with extra `-D` flags, used for the special builds. |
| `_eng.py` | Shared helper: engine lookup, timed pipe reading, hang protection. |

## Data

| File | Content |
|---|---|
| `golden_depth8*.json` | Reference searches for `t_golden.py`: the default build and other build variants. |
| `ordering_ref_*.json` | Reference counters for `t_ordering.py`. |
| `wac.epd` | Win At Chess, 300 tactical positions. |
| `endgames.epd` | Endgame start positions for `match.py --epd`. |
| `books/noob_3moves.epd` | Default opening book of `match.py` (from the Stockfish book collection, CC0). |
