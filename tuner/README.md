# Evaluation tuner

These scripts tune the parameters of Zeval's static evaluation (`src/eval.asm`) on labelled chess positions. The method is
Texel tuning: logistic regression of game results on the evaluation. The search code is not touched; a tuned
evaluation becomes part of the engine only after it is written into `src/eval.asm`, rebuilt and accepted by an SPRT.

## Contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Files](#files)
- [Step 0: check the tooling](#step-0-check-the-tooling)
- [Step 1: training data](#step-1-training-data)
- [Step 2: extract features](#step-2-extract-features)
- [Step 3: tune](#step-3-tune)
- [Step 4: apply and build](#step-4-apply-and-build)
- [Step 5: test in games](#step-5-test-in-games)
- [Reading the tuner output](#reading-the-tuner-output)
- [Evaluation switches](#evaluation-switches)
- [Constants outside the linear model](#constants-outside-the-linear-model)
- [Adding an evaluation term](#adding-an-evaluation-term)
- [Troubleshooting](#troubleshooting)

## How it works

The evaluation is linear in its parameters. For every position it is a tapered sum of:

- piece-square values with material merged in (6 piece types x 64 squares),
- the bishop pair,
- the terms of the full evaluation (mobility, pawn structure, passed pawns, king safety, threats, ... : the `evp` table of
  `src/eval.asm`),
- and tempo.

Each parameter has a middlegame and an endgame value, 1611 numbers in all. `model.py` is an independent Python
implementation of the same evaluation. It turns each position once into sparse feature counts (White minus Black), so the
tuner never has to run the engine.

The tuner fits

```
p    = sigmoid(K * eval / 400)            (White's view)
loss = mean (result - p)^2                (result 1 / 0.5 / 0)
```

`K` is fitted once on the starting parameters and then kept fixed, so the centipawn scale of the engine stays the same.
The loss is minimised with Adam or L-BFGS. Several safeguards are there for small, self-generated data sets:

- the positions of one game are never split between training and validation,
- training stops early on the validation loss,
- an L2 prior pulls every parameter towards its starting value,
- the loss is also evaluated with the rounded integer values the engine will actually use.

A lower loss only means that the evaluation predicts results better. Whether it plays better is decided by an SPRT
match (step 5).

## Requirements

- Python 3.8 or newer with `numpy`, `scipy` and `python-chess`: `pip install numpy scipy chess`
- A Zeval build (see the main README). For `selftest.py --build`, Linux with `nasm` and `ld`.
- Memory: about 260 MB per million positions in a feature set.

Run every command from the root of the checkout. On Windows, use `python` and backslashes (`tuner\tune.py`), and
`zeval.exe` or `dist\zeval.exe` as the engine. `check_eval.py`, `micro_tests.py` and `selftest.py` find the engine like
the tests do: `ENGINE`, then `zeval` in the root, then `dist/`. The other scripts take `--engine`. Every script lists
all of its options with `--help`.

Generated files go into `tuner/data/`, which git ignores.

## Files

| Script | Purpose |
|---|---|
| `model.py` | The evaluation as a linear model: position to features, a bit-exact integer copy of the engine's evaluation, the reader for `src/eval.asm`. |
| `check_eval.py` | Compares `model.py` with the engine's `eval` command on random positions. Must report 0 mismatches. |
| `micro_tests.py` | Tests of the whole pipeline against independent references (python-chess, closed forms). |
| `selftest.py` | Gradient check, recovery of a planted evaluation on synthetic data, apply and rebuild. |
| `gen_data.py` | Self-play games for training data (rounds of `tests/match.py`, every engine move saved). |
| `resolve.py` | Turns self-play games into training positions: positions searched to a fixed depth, PV played out, labelled with the game result. |
| `extract.py` | Game files and labelled position files to a feature set (`.npz`). |
| `tune.py` | The tuner. |
| `apply_params.py` | Writes tuned values into `src/eval.asm`, with a safety gate and `--revert`. |
| `fit_scale.py`, `fit_special.py`, `fit_init.py` | Fit the constants that are not linear parameters (endgame scale rules, special endgames, initiative). |

## Step 0: check the tooling

Run these once on a new checkout and again after any change to the evaluation or the scripts:

```sh
python3 tuner/check_eval.py              # "0 mismatches"                         (a few seconds)
python3 tuner/micro_tests.py             # "29 of 29 passed"                      (about 2 minutes)
python3 tuner/selftest.py --build        # "SELFTEST PASSED"; omit --build on Windows (about 4 minutes)
```

If `check_eval.py` reports mismatches, the model and the engine disagree, and nothing the tuner produces can be trusted.

## Step 1: training data

The tuner needs positions labelled with a game result. There are three sources, and they can be mixed.

### A. Self-play games

```sh
python3 tuner/gen_data.py --engine dist/zeval --rounds 10 --games 2000 --movetime 50 --workers 8 --seed0 401
```

- Writes `tuner/data/sp_<seed>.jsonl`, one file per round. Every round uses a new seed, so new openings.
- Each game gives about 80 usable positions. 12,000 to 15,000 games give about one million.
- Set `--workers` to the number of physical cores. Each engine runs with one thread.
- Ctrl-C is safe: finished rounds stay on disk. To continue, start again with a higher `--seed0`.
- Games are adjudicated by default, so part of the labels comes from the engine's own score. `--no-adj` plays every game to
  its end (slower). `--no-draw-adj` keeps win adjudication but plays drawn positions out.

These files go straight into step 2.

### B. Positions the engine reaches in its own search

This is the usual way to get good training data from your own games. Games are played on a clock. A few positions per
game are searched to a fixed depth, the principal variation is played on the board, and the position at its end is
labelled with the game result.

```sh
python3 tuner/gen_data.py --engine dist/zeval --tc 2+0.02 --book auto --no-draw-adj \
        --rounds 50 --games 2000 --workers 8 --seed0 20001 --out tuner/data/own
python3 tuner/resolve.py --engine dist/zeval tuner/data/own --out tuner/data/own.epd --depth 12 --per-game 10 --workers 8
```

- `--book auto` starts the games from `tests/books/noob_3moves.epd`. A game at 2+0.02 takes about 6 to 7 seconds.
- `resolve.py` keeps about 95 % of the positions it searches, so the data set is roughly games x `--per-game` positions.
  It handles about 6 positions per second per worker at depth 12.
- A PV that ends inside an exchange is completed with the engine's capture-only quiescence search, so the labelled
  positions are quiet.
- `resolve.py` can be stopped and started again with the same arguments. It continues where it stopped.
- The output is one position per line, `FEN [result] game=FILE:LINE`. The `game=` tag keeps the positions of one game
  together for the validation split.

### C. External labelled positions

`extract.py` reads labelled position files, one position per line:

```
<FEN> [1.0]                   result in brackets: [1.0] [0.5] [0.0], or any fraction
<FEN> c9 "1-0";               result as an EPD opcode: "1-0", "1/2-1/2", "0-1"
```

By default, results are read from White's point of view. Add `--stm-wdl` to `extract.py` when they are from the side to
move. A set that is not quiet needs `--qsearch` (and `--filter-in-check`). Several files with different settings can be
listed in a CSV file for `--sources` (`path,wdl_from_side_to_move(0/1),limit`).

Self-play data teaches the engine its own blind spots. External data, with labels from other engines or real games,
balances it. A 50 / 50 mix is a good first try.

**Keep data seeds and test seeds apart.** Never test a tuned engine on the openings it was tuned on. `tests/match.py`
draws a fresh seed when `--seed` is not given.

## Step 2: extract features

```sh
python3 tuner/extract.py tuner/data/d.npz tuner/data/sp_*.jsonl --per-game 25      # self-play games (A)
python3 tuner/extract.py tuner/data/own.npz tuner/data/own.epd                     # resolved positions (B)
python3 tuner/extract.py tuner/data/mix.npz tuner/data/own.epd external.epd        # a mix
```

- Game data is filtered to quiet positions: the side to move is not in check, the move played was neither a capture nor
  a check, and the score is not a mate score.
- Positions with fewer than `--min-pieces` (6) pieces and positions handled by a special endgame evaluation are left out.
  Duplicates are merged and their labels averaged.
- `--per-game N` keeps at most N random positions per game, which gives less correlated data.
- `--drop-adjudicated` keeps only games that reached a real end.
- The last line reports the positions, duplicates merged and games. The `stats` line counts what was dropped. Many
  `bad` lines mean a label format that was not recognised.

A feature set belongs to the evaluation it was extracted with. Re-extract after any change to the terms or the
switches of the evaluation.

## Step 3: tune

Tune in two stages.

**Stage 1: bishop pair and tempo only (low risk).**

```sh
python3 tuner/tune.py tuner/data/d.npz --out tuner/data/p_stage1.json --free bp,tempo --bootstrap 50
```

- The line `sanity: correlation(...)` must be clearly positive, typically +0.4 to +0.6. A negative value means
  the labels are from the wrong side: use `--stm-wdl` in step 2.
- `--bootstrap 50` refits on 50 resamples of the games and prints mean +- sd for every value. A change smaller than
  about 2 sd is noise: get more data before you apply it.
- Note the printed `K` value and pass it as `--k` in the next stage.

**Stage 2: all parameters.**

```sh
python3 tuner/tune.py tuner/data/d.npz --out tuner/data/p_all.json \
        --k <K from stage 1> --prior-sweep 20,60,200,600,2000,6000 --norm-scale asm --optimizer lbfgs
```

- **`--prior-sweep`** tries several prior strengths. Each prior is a number of pseudo-observations pulling a parameter
  towards its starting value. For every prior, the tuner prints the validation loss and a bootstrap interval over the
  validation games. The best prior goes to `p_all.json`, the others to `p_all_prior<P>.json`.
  - The "suggestion" line names the largest prior that cannot be told apart from the best one. That one overfits less.
  - With little data (under about 100,000 positions), only large priors (2000 to 6000) help.
  - For runs with a few thousand games, use `--val 0.1` to `0.2`.
- **`--norm-scale asm`** rescales the result so that the average |eval| over the training positions stays that of the
  current tables. The search compares the evaluation with absolute centipawn margins (futility, razoring, null move,
  ...). A tuned evaluation that is a few percent larger or smaller loses Elo for that reason alone, and the SPRT would
  measure the scale shift instead of the tuning. Leave it out only if you plan to retune the search margins afterwards.
- **`--free`** selects which parameters move; all others keep their values. You can give:
  - `all`, `pst`, one piece table (`p`, `n`, `b`, `r`, `q`, `k`), `bp`, `tempo` or `full`;
  - a term group: `mob`, `mob_pins`, `pawns`, `passed`, `rooks`, `king`, `pieces`, `threats`, `kdanger`, `passed2`,
    `pawns2`, `shelter`, `pieces2`, `imbalance`, `space`, `klines`;
  - one term by its name in `model.TERMS`;
  - several of these, comma separated.

  A newly added term is usually tuned alone first, with everything else fixed.
- **Other options:**
  - `--optimizer lbfgs` reaches the same optimum as Adam in far fewer passes.
  - `--lam 0.3` blends the engine's own search score into the target. It needs game data from source A.
  - `--seed` chooses the validation split.

## Step 4: apply and build

```sh
python3 tuner/apply_params.py tuner/data/p_all.json        # patches src/eval.asm; the original is saved as tuner/eval.asm.orig
sh build/build.sh                                          # Windows: build\build.bat
cp dist/zeval zeval_tuned                                  # keep the tuned build; rebuild the baseline later with --revert
python3 tuner/check_eval.py --engine zeval_tuned           # 0 mismatches: the tables were written and built correctly
python3 tests/t_eval.py --engine zeval_tuned               # symmetry, incremental evaluation, tempo
```

`apply_params.py` refuses to write, and exits with code 1, when any of these is true:

- the file has no baseline from `tune.py`;
- the rounded values have a worse validation loss than the current tables;
- the average |eval| collapsed or exploded (outside 0.5x to 2x).

`--force` overrides the gate. The gate only stops broken output; it says nothing about strength.

`--out-dir DIR` writes `DIR/eval.asm` and leaves `src/` alone. `--revert` restores the saved original. The backup is made
only once, so delete `tuner/eval.asm.orig` before you tune a newer version of the source.

The evaluation changed, so `tests/t_golden.py` will now report differences. That is expected. Re-record the reference
only when the tuned values become the default:

```sh
python3 tests/t_golden.py --make dist/zeval tests/golden_depth8.json
```

## Step 5: test in games

```sh
python3 tuner/apply_params.py --revert && sh build/build.sh       # dist/zeval = the baseline again
python3 tests/match.py zeval_tuned dist/zeval --tc 10+0.1 --workers 8 --elo0 -3 --elo1 3
```

- Run the match twice, each time with a new seed (leave `--seed` out), with one thread per engine and a time control of
  at least 10+0.1.
- If it passes, apply the values again and make them the default.
- If it fails, change the data or the prior and try again. A failed tune is more often a data or weighting problem than
  a bad term.

## Reading the tuner output

| Line | What to look for |
|---|---|
| `sanity: correlation(...)` | Clearly positive. Negative means the labels are from the wrong side. |
| `start / tuned, float / tuned, rounded (ENGINE)` | Training and validation loss. The validation loss of the rounded values must go down. If the training loss falls and the validation loss does not, there is too little data or the prior is too small. |
| `validation loss change of the ROUNDED tables` | A 95 % interval over the validation games. An interval that contains 0 means the change is noise. |
| `eval scale` / `mean\|eval\|` | How much the overall scale of the evaluation moved. Judge scale by this line, not by `K`: K also rises when the evaluation simply predicts better. |
| `largest changes` | An absurd jump (a pawn square +60, say) means the prior is too small. |
| `coverage` | Parameters that never or rarely occur in the data. They only feel the prior. |

## Evaluation switches

Many terms sit behind build switches in `src/eval.asm`; their bits are listed in `FLAG_BITS` in `model.py`. The tuner
must use the switches of the build the values are meant for:

- `check_eval.py` reads them from the engine (`evalcfg`), so there is nothing to set.
- `extract.py --switches EV_X=1,EV_Y=1` sets them for a data set. The default is the defaults in `src/eval.asm`. The
  feature set stores them and `tune.py` uses them.
- Build the engine with the same flags: `NASMFLAGS="-DEV_X=1 -DEV_Y=1" sh build/build.sh`.

The `evp` table always holds the values of every term. A switched-off term simply has no features and keeps its values.

Parts of the evaluation that are not linear are left out of the features:

- **Lazy evaluation and initiative (`EV_LAZY`, `EV_INIT`, `EV_GRAIN`).** Generate data and tune with them off.
  `EV_TUNEMODE=1` turns off every non-linear approximation in one switch.
- **The endgame scale factor.** It depends on the position only, so it enters the model as a fixed weight per position.
- **Special endgame evaluations (`EV_ENDG`).** These positions are excluded.

## Constants outside the linear model

Some constants are not linear parameters. `tune.py` cannot fit them, so they have their own fitting scripts. Each one
uses the tuner's loss and `K`, keeps the tables fixed, and writes the fitted constants as JSON for `src/eval.asm`:

```sh
python3 tuner/fit_scale.py   --engine dist/zeval --data a.epd b.epd --n 400000 --tb <syzygy dir> --out scale.json   # SC_*:  endgame scale rules
python3 tuner/fit_special.py --tb <syzygy dir> --out special.json                                               # EG2_*: KQ-KR, KR-KB, KR-KN
python3 tuner/fit_init.py    --engine dist/zeval --data a.epd b.epd --out init.json                             # IN_*:  initiative
```

- `--tb` needs Syzygy WDL tables.
- `--cache FILE` stores the reduced data, so a refit takes seconds.
- A good order is: fit the constants, retune the tables, then refit the constants on the new tables.

The search margins and other build constants are tuned with SPSA on a `-DTUNE` build (`tools/spsa.py`), not with this
tuner.

## Adding an evaluation term

1. In `src/eval.asm`, give the term an index after the existing ones (`%define EV_...`) and raise `EV_N`. Add its
   entries to `evp` at the same position, and count the feature in `ev_side`, or in `ev_sideb` for a term that needs the
   attack maps of both sides. The black side uses the mirrored board, so nothing is written twice.
2. In `tuner/model.py`, add the term to `TERMS` at the same position with the same number of entries. Count it in
   `side_terms` (or `side_terms_b`) by the same definition.
3. Rebuild. `check_eval.py` must report 0 mismatches and `tests/t_eval.py` must pass before any tuning.
4. Re-extract the data, tune the new term alone (`--free <term>`), then retune everything, apply, and run an SPRT.

A term that is a product of two features cannot be tuned directly. Express it as a table indexed by a count, the way
`kdanger` does.

## Troubleshooting

| Problem | Fix |
|---|---|
| `check_eval.py` reports mismatches | The model and the engine disagree, for example a changed term or a build with other switches. Fix this before anything else. |
| `validation split is empty` | Too little data. Use more games, or `--val 0.2`. |
| `WARNING: the eval hardly correlates` | The labels are from the wrong side (`--stm-wdl`) or the input is broken. |
| `apply_params.py` refuses | The tuned values do not beat the current tables on validation. Use more data or a larger prior. |
| An old `.npz` or `params.json` fails to load or apply | It was made for another set of terms (`EV_N`). Re-extract and retune. |
| Out of memory | About 260 MB per million positions. Use `--limit` / `--per-game` in `extract.py`, or a subset. |
