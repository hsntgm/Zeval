# Changelog

Changes to the engine, the tests, the tuner and the tools. Documentation-only changes are not listed.

## Unreleased (branch `dev/1.1`, since 1.0)

The engine still identifies itself as Zeval 1.0. The version changes with the 1.1 release.

### Search

- **Staged move picking** (`STAGED`, default 1; implies `TTFIRST`): a node searches the TT move before any move is generated
  (`tt_pseudo` checks on the bitboards that the move is in the generator's list), then the good captures (generated alone), then the
  killers (checked with `tt_pseudo`), and generates the full list only for the quiet moves and the bad captures. The order of the score
  bands is kept, and the moves picked in the first stages are swapped to the front of the full list as `pick()` would have left them.
  A cutoff before the quiet moves costs no quiet generation and no history lookup: 6.2 % fewer instructions per node with a tree of the
  same size (900 positions at depth 11), +16.5 ± 9.2 Elo against the search without stages (2 × 1000 games, 3+0.03), same tactics solve
  rate. Candidates: `S5_NOSTAGED` turns it off, `S3_TTFIRST` keeps only the TT move stage (+1.7 ± 9.2 Elo).
- **Two speed-only changes of capture scoring** (`CAPIDX`, `QSSEE`, default 1): `score_moves` looks up the capture-history entry of a
  capture once instead of twice (`CAPIDX`), and quiescence skips the second `see_ge` for a capture that `score_moves` already found to
  lose (`QSSEE`). The search is unchanged (golden identical); together 0.9 % less time at the same nodes. Candidate `S4_NOSEEREUSE`
  turns both off.

- **Capture history** (`CAPHIST` 2, default):
  - A table `[piece][to square][victim]` per thread, cleared by `ucinewgame`.
  - A capture that causes a cutoff gets min(120 · depth, 1200) as a bonus. Every capture searched before the cutoff move gets the
    same amount as a malus, also when a quiet move cuts.
  - The history is added to the MVV-LVA score in the main search and in quiescence.
  - With `CAPHIST` 2 the good / bad split of captures follows the history: a capture is good when SEE ≥ −10 − history / 64.
  - Result: +9.4 ± 13.7 Elo in 1000 games at 3+0.03 against the 1.0 search.
- **New switches, off by default** (measured and not taken; status in [docs/README.md](docs/README.md)):
  - `NEG_EXT`: negative extension of a TT move that is neither singular nor a multi-cut.
  - `PROBCUT`: ProbCut at non-PV nodes from depth 5.
  - `CAP_LMR`: late captures with a low capture history searched one ply shallower.
  - `LMP_SF`, `PR_MAXD`, `RFP_MAXD`: an alternative late move count and the depth limits of the late-move and reverse futility pruning as switches.
- **`-DTUNE` builds:**
  - 23 more constants are UCI options (`T62` .. `T84`): singular margins, SEE pruning, history and continuation bonuses, capture history,
    the LMR table, aspiration window, pruning depth limits, late move count, IIR depth, the capture-ordering SEE limit and the time curve.
  - Values that broke the search are guarded: an aspiration window of 0, LMR table entries outside 0 .. 63, and time curve values below 1.
  - Negative option values are accepted.
  - A `-DTUNE` build at the default values searches exactly as the release build.

### Evaluation

- **Open lines against the enemy king** (`EV_KLINES`, default): on the king's file and its two neighbours, files without any pawn in
  front of the king, own pawns storming within three ranks of it, and own rooks and queens on files without a shelter pawn. Middlegame
  only. Result: +6.6 ± 14.0 Elo in 1000 games at 3+0.03; tactics results unchanged.
- **Retuned values of switches that are off:**
  - `EV_IMBAL` (material imbalance) and `EV_INIT` (initiative) were tuned from zero.
  - Neither is the default: `EV_IMBAL` −23.2 ± 18.8, `EV_INIT` −1.5 ± 14.1.
- **`EV_OSCALE`** (test builds): scales the evaluation by N/1024.

### UCI output

- The info line printed after the helper threads have stopped now counts the nodes of every thread. Before, nodes and nps could drop
  in the last line.
- `nps` is computed from the same node count as `nodes`, as nodes · 1000 / max(time, 1).
- When the Lazy SMP vote picks a helper's move, its seldepth is shown together with its depth, score and line.

### Fixes

- **`EV_INIT`:** the "almost unwinnable" rule now also requires that no passed pawn is on the board.
- **`EV_IMBAL`:** two entries that repeated the bishop-pair term are fixed at 0.
- **50-move rule and checkmate** (`R50MATE`, default 1): a node at half-move clock 100 was a draw even when the side to move was
  checkmated, so a mate given on the 100th half-move was not seen (mate in 1 with the clock at 99, mate in 2 at 97). Such a node is now
  a draw only when the side to move is not checkmated. `R50MATE` 0 builds the old rule.
- **Pre-make pruning test** (`PREPRUNE`): it used a fixed depth limit and late move count. With `PR_MAXD` below 6 it pruned moves that the
  pruning after the move no longer covers. Both tests now share one definition; the default search is unchanged.
- **`tests/match.py`:**
  - Games ended by a threefold repetition or the 50-move rule that could only be claimed on the next move. Now they end only once it
    happened on the board.
  - A forced move answered without a score line was recorded with score 0. It now keeps the previous score.
- **`tuner/tune.py`:** `--seed` had no effect on the validation split; it now selects a different split.
- **`tools/spsa.py`:**
  - Negative values were clipped to 0, and the folder of `--out` was not created.
  - The start-up check refused parameters that are in use but act only in some positions or deeper searches (endgame scale, history
    bonus limits, rare thresholds), and named a switch that is off as the reason. A `-DTUNE` build now declares as options `T<n>` the
    slots its code uses; the check refuses only a parameter the build does not declare, and a search that does not change gives a note.
  - The check waited without limit for an engine that stopped answering; it now stops with an error.
- **`--help`:** fixed for `tuner/tune.py` and `tests/t_eval.py`; both stopped with an error.
- **Build:** `build/build.bat` can link with MSVC `link.exe`.
- **Tests:**
  - Tests, tactics runner and verify scripts find an engine built into `dist/`, so a fresh checkout works after building.
  - Several checks could not fail:
    - `t_uci` compared Hash sizes at a depth where every size gives the same tree;
    - `t_pv` ignored the expected mate distance;
    - `t_robust` accepted a missing answer;
    - `t_chshared` compared searches of different length.

### Test and match tools

- **`tests/match.py`:**
  - `--book auto|random|FILE` starts games from an EPD book. The default `auto` uses `tests/books/noob_3moves.epd` (added).
  - `--no-draw-adj` keeps win adjudication but plays drawn games to their end.
- **New `tests/cand_matrix.py`:** every candidate build against the default on several seeds, with a pooled summary and LOS. A stopped run
  continues where it stopped.
- **New and extended checks:**
  - `t_smp`: the node count of the info lines never goes down within a search, and Threads 1 after a 32-thread search matches a fresh process.
  - `t_pv`: nps against nodes and time, and the exact mate distance.
  - `t_perft`: the published perft totals of the six standard positions.
- **`t_ordering`** reports the capture-history counters.
- **Diagnostic builds:**
  - `-DHSTAT` (console command `hstat`): counters of continuation-history pruning and of the LMR history step.
  - `-DPCVERIFY` (`pcstat`): ProbCut without cutting, counting how often a would-be cut is refuted.
- **New `tools/build_testbuilds.sh` / `.bat`:** build the test builds used by `verify_smp.bat` and `verify_units.bat`.
- **New `tests/t_ttfirst.py`:** for `TTFIRST` alone (`STAGED` 0): a build that generates eagerly reproduces the search without stages,
  `tt_pseudo` agrees with the generator at every node (also for bit flips and random values), garbage TT moves, a reference
  `golden_depth8_ttfirst.json`; `--selftest` breaks the validator 12 ways.
- **New `tests/t_staged.py`:** for `STAGED`: the default golden, every move of a staged node picked exactly once (`-DSTAGEVERIFY`),
  garbage killers and TT moves (`-DSTGFUZZ`, `-DTTFUZZ`); `--selftest` breaks the stages 5 ways.
- **`ORDSTAT`** counts the position of a cutoff move over the whole node with `STAGED` (the list restarts with each stage).
- **New reference `tests/golden_depth8_nostaged.json`:** the search without stages (`-DSTAGED=0`), which the older references
  (`golden_depth8_zeval10.json` and the others) now need in their build flags.
- **`tests/t_smp_audit.py`** takes `-D` switches, to audit a build with a switch on.
- **New `tests/t_seereuse.py`:** `CAPIDX` / `QSSEE` reproduce the golden, and every capture marked as losing is refused by `see_ge`
  (`-DQSSEEVERIFY`); `--selftest` breaks the mark 2 ways.
- **New `tests/t_tune.py`:** every read of the option table is marked, and a `-DTUNE` build declares its `T<n>` options with the current
  values.
- **`tests/match.py`:** an engine that fails the start-up handshake is stopped instead of left running.

### Tuner

- **New `tuner/resolve.py`:** training positions from the engine's own games.
  - Random positions are searched to a fixed depth, the principal variation is played on the board, and its end is resolved with the
    quiescence search and labelled with the game result.
  - The run can be stopped and continued.
- **`tuner/gen_data.py`:** `--tc` (clock games), `--book` and `--no-draw-adj`.
- **`tuner/extract.py`:** keeps the positions of one game in one validation group (the `game=` tag).
- **`tuner/tune.py`:** `--norm-scale` rescales a tuned evaluation to the mean |eval| of the current tables, so that a retune is measured
  without a change of scale.
- **`tuner/apply_params.py`:** warns when the mean |eval| moved by more than 2 %.
- **New `tuner/fit_init.py`:** fits the initiative constants of `EV_INIT` to game results.
- **New `tuner/micro_tests.py`:** tests of the tuning pipeline against independent references.
- **`tools/spsa.py`:**
  - Parameter bounds.
  - The mean of the last iterations as the result.
  - A start-up check that the build uses every parameter.
  - Failed engines counted.
  - `--resume` checks its file.
  - `--book`.

### Defaults compared with 1.0

The default engine differs from 1.0 by `CAPHIST` 2, `EV_KLINES` and `STAGED` (and by the speed-only `CAPIDX` / `QSSEE`, which search
the same tree). The fixed-depth reference search (`tests/golden_depth8.json`, 60 positions at depth 8) changed from 2,484,151 to
2,397,486 nodes.
