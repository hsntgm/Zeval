# Search and evaluation reference

This document maps every build switch and every tunable constant of the search and the evaluation. For each one it gives the default
value and the current test status. The source is the authority: the switches are defined and described in the headers of
`src/p1_data.asm` (search) and `src/eval.asm` (evaluation).

## Conventions

**Build switches** are assembler defines that select code at build time:

```sh
NASMFLAGS="-DSING=0 -DEV_SPACE=1" sh build/build.sh       # Linux
set NASMFLAGS=-DSING=0 -DEV_SPACE=1 & build\build.bat     # Windows
```

**Tunable constants** are immediates in the release build. A `-DTUNE` build reads them from the UCI options `T0` .. `T84`, which is
what `tools/spsa.py` and `tests/match.py --optA "T0=90"` use. In the tables below, the `T` column gives the option index.

**Status values:**

| Status | Meaning |
|---|---|
| Default | On in the release build. |
| Retest | Off. The evidence is weak or comes only from very short time controls; it is worth a long time control SPRT. |
| Borderline | Off. The results are close to zero; it is a candidate for a package test or a retune. |
| Low priority | No proof either way: only a 300-game screen, or a long run close to zero. The door stays open. |
| Closed | Off. Clearly negative, or no signal in the data. Do not retest without a new idea. |
| Speed | Does not change the search tree; it is measured with `tests/bench_ab.py`, not in games. |
| Diagnostic | For analysis and test builds only. |

**Results** are self-play Elo against the default build of the time, with Threads 1 unless noted. They are written as
`Elo ± 95 % interval (games, time control)`, where `20 ms` means 20 ms per move. Results from 200 games at 20 ms are
screening only. A result needs about 1,000 games to resolve ±14 Elo, and changes of a few Elo need several thousand games.

**Candidate builds** in `candidates/FLAGS.txt` change one thing against the default, and `sprt_candidate.bat NAME` tests them:

| Prefix | Meaning |
|---|---|
| `A` | search ablations |
| `X` | ablations or parameter changes |
| `E` | evaluation candidates (`E0` = material and PST only) |
| `M` | multi-thread |
| `S` | speed |

Evaluation candidates keep a fixed number, `E1` .. `E21`. The next free number is `E22`.

---

## 1. Search

### 1.1 Core (always on)

| Component | Implementation | Constants |
|---|---|---|
| Iterative deepening | Main thread drives iterations; Lazy SMP helpers search the same root | Threads 1 .. 32 |
| Aspiration windows | From depth 5; the step doubles after each fail, full window once it exceeds 400 cp | `ASP_W` 20 cp (T73) |
| Principal variation search | Null-window search of the later moves, re-search on fail-high | |
| Transposition table | Lock-free (key XOR data), 16-byte entries in 2-entry buckets, 6-bit age, depth / age replacement; also probed in quiescence | Hash 32 MB default |
| Static eval in the TT | Stored with the entry and reused on a hit (`EVCACHE`) | |
| Mate distance pruning | At every node except the root | |
| Check extension | A node in check is searched one ply deeper | |
| Internal iterative reduction | Without a TT move: depth − 1 from depth `IIR_D` | `IIR_D` 4 (T77) |
| Quiescence search | Captures and promotions; all evasions when in check; stand pat | |
| Delta pruning | Skip a capture when stand pat + victim (+800 for a promotion) + margin ≤ alpha | `DELTA_M` 200 (T58) |
| Late move pruning | Late quiet moves of non-PV nodes; move count (d² + `LMP_B`) ± a quarter, depending on improving | `LMP_B` 5 (T76), up to depth `PR_MAXD` 6 (T74) |
| Futility pruning | eval + 110 + 90 · d ≤ alpha, on the LMR-reduced depth | 110 (T4), 90 (T5) |
| Reverse futility pruning | Non-PV, not in check: eval − (85 · d − 35 · improving) ≥ beta | 85 (T0), 35 (T1), up to depth `RFP_MAXD` 6 (T75) |
| Null move pruning | Non-PV, not in check, depth ≥ 3, eval ≥ beta, non-pawn material; R = `NMP_RB` + d/4 + min(599, eval − beta)/`NMP_EDIV` | `NMP_RB` 3 (T2), `NMP_EDIV` 200 (T3) |
| Late move reductions | Table R = 0.75 + log2(d) · log2(m) · 0.2135, from move `LMR_MINM` 4; modified by the LMR switches below | 750 (T71), 2135 (T72) |
| Move ordering | TT move, good captures (MVV-LVA + capture history), 2 killers, quiet moves (butterfly history + continuation history), bad captures | |
| Quiet history | Butterfly table [side][from][to]; bonus min(16 · d², 1600), gravity, ±16000 | 16 (T67), `HIST_BMAX` 1600 (T68) |
| Draw detection | Repetition, fifty-move rule, insufficient material | |

### 1.2 Search switches

**Pruning and node-level decisions**

| Switch | Default | What it does | Status | Evidence |
|---|---|---|---|---|
| `RAZOR` | 1 | Razoring at depth ≤ 3: eval + `RAZOR_B` + `RAZOR_M` · d² < alpha → qsearch result | Default | +23.2 ± 29.5 alone (300, 30 ms); pruning group +20.9 ± 36 (200, 30 ms) |
| `RAZOR_SQ` | 1 | Razor margin grows with d² (0 = linear in d) | Default | quadratic +23.2 ± 29.5, linear +33.7 ± 27.1 (300 each, 30 ms), neither significant |
| `RFP_EXT` | 1 | Reverse futility only without a TT move or with a capture TT move; returns (661 · beta + 363 · eval) / 1024 | Default | same group |
| `FUT_LMRD` | 1 | Futility pruning on the LMR-reduced depth | Default | same group |
| `NMP_TTG` | 1 | No null move when the TT already proves score < beta | Default | same group |
| `TTCUT_COND` | 1 | A TT cutoff needs cutNode == (ttScore ≥ beta) or depth > 4 | Default | same group |
| `EVAL_TT` | 1 | Eval used by pruning = TT score when its bound allows it | Default | +56.1 ± 35 alone (200, 30 ms) |
| `NMP_CUT` | 1 | Null move only at expected cut nodes | Default | +10.4 ± 35 alone (200, 30 ms) |
| `SEE_PC` | 1 | Prune captures with SEE < −`SEE_CM` · d² (non-PV, not in check, depth ≤ `SEE_MAXD` 6) | Default | the SEE group against no SEE: +41.7 ± 31 (310, 40 ms) |
| `SEE_QS` | 1 | Quiescence skips captures with SEE < 0 (not in check) | Default | SEE group |
| `CH_PRUNE` | 1 | Prune late quiet moves with continuation history < −`CH_PM` · d at depth ≤ `CH_PD` | Default | part of continuation history; ablation `X6_CHNOPRUNE` |
| `IIR_CUT` | 0 | IIR only at PV / cut nodes and depth ≥ `IIR_MIND` | Closed | −31.4 ± 33 (200, 30 ms); a full ply shallower at equal time |
| `NMP_VER` | 0 | Verification search at depth ≥ `NMP_VERD` (14) | Closed | not measured alone; no gain seen in any group |
| `SEE_PQ` | 0 | Prune quiet moves with SEE < −`SEE_QM` · d | Closed | tried, no gain |
| `PROBCUT` | 0 | ProbCut: captures searched against beta + `PC_M` at depth − `PC_R` | Closed | −13.2 ± 19.6 (500, 3+0.03); the cuts are already found by null move and RFP |

**Reductions**

| Switch | Default | What it does | Status | Evidence |
|---|---|---|---|---|
| `LMR_CUT` | 1 | R + `LMR_CUTP` at cut nodes | Default | LMR group: +31.4 ± 35 (200, 30 ms) |
| `LMR_IMPR` | 1 | R + 1 when not improving (2: also R − 1 when improving) | Default (1) | value 2 never accepted |
| `LMR_HIST` | 1 | R ∓ 1 for quiet history beyond ±`LMR_HT` | Default | LMR group |
| `LMR_TTC` | 1 | R + 1 when the TT move is a capture | Default | LMR group |
| `LMR_RES` | 1 | After a reduced fail-high: re-search 1 ply deeper or shallower depending on the margin | Default | LMR group |
| `LMR_MINM` | 4 | First reduced move number (3 = one move earlier) | Default (4) | 3 was not part of the accepted LMR group |
| `CH_LMR` | 0 | LMR history step on the whole ordering score instead of the butterfly history | Closed | −14.3 |
| `LMR_BADCAP` | 0 | Reduce bad captures like quiet moves | Closed | −5.6 ± 19.7 (500, 3+0.03) |
| `CAP_LMR` | 0 | Reduce late captures with capture history < `CAP_LMRT` | Closed | −1.4 ± 18.0 (500, 3+0.03) |
| `LMP_SF` | 0 | Late move count 3 + d², halved when not improving | Borderline | +3.5 ± 13.1, 12.6 % fewer nodes; with wider pruning depths +2.1 ± 13.3 |

**Extensions**

| Switch | Default | What it does | Status | Evidence |
|---|---|---|---|---|
| `SING` | 2 | Singular extension of the TT move with multi-cut: exclusion search at (d − 1)/2 against ttScore − `SE_M` · d (1 = test runs, result unused) | Default | +16.7 ± 12.3 (1184, 5+0.05) |
| `SE_MC` | 1 | Multi-cut part of `SING` 2 | Diagnostic | without it the tactical loss of `SING` grows |
| `NEG_EXT` | 0 | Negative extension of a TT move that is neither singular nor multi-cut (2: also at expected cut nodes) | Closed | −2.7 ± 11.7 (1310), −4.3 ± 17.0 (650), 3+0.03 |
| `CHK_EXT` | 0 | Extra extension of checking moves (1 safe checks, 2 all) | Closed | −72.7 ± 46 (160, 40 ms) |

**Move ordering and history**

| Switch | Default | What it does | Status | Evidence |
|---|---|---|---|---|
| `SEE_ORD` | 1 | Good / bad capture split by SEE | Default | SEE group |
| `SEE_BADLAST` | 1 | Bad captures after the quiet moves | Default | SEE group |
| `CONTHIST` | 1 | Continuation history, 1 and 2 plies back, in ordering and in `CH_PRUNE` | Default | +9.1 ± 11.9 (1600, 60 ms); ablation `X5_NOCONTHIST` |
| `CH_BL` | 1 | Continuation bonus linear in depth: min(`CH_BA` · d, `CH_BMAX`) | Default | part of `CONTHIST`; the ordering statistics get worse without it |
| `CAPHIST` | 2 | Capture history [piece][to][victim] in capture ordering; 2 also moves the SEE good / bad split by the history | Default (2) | +9.4 ± 13.7 (1000, 3+0.03); value 1 alone −2.8 ± 14.0 |
| `CAP_SEEPOS` | 1 | `CAPHIST` 2: good history may also keep a slightly losing capture in front | Default | part of `CAPHIST` 2 |
| `EVALDIFF_HIST` | 0 | Update the history of the opponent's last move by the change of the static eval | Closed | −111 ± 38 (200, 30 ms) |
| `CH_SHARED` | 0 | One continuation history table shared by all threads | Retest | −12.5 ± 14.4 (1000, Threads 2, 40 ms); open for 8 or more threads |

**Time management** (real clock only; `go movetime`, `depth`, `nodes` and `infinite` are not affected)

| Switch | Default | What it does | Status | Evidence |
|---|---|---|---|---|
| `TM_CURVE` | 1 | Time per move follows the game ply: share `TMC_F0` → `TMC_F1` over `TMC_PM` plies of a `TMC_H`-move horizon, cap clock/`TMC_CAP` | Default | +40.1 ± 20.1 (296, 3+0.03); +17.4 ± 25.8 (180, 10+0.1) |
| `TM_STAB` | 1 | Shorter when the best move is stable for `TM_STABN` (8) iterations | Default | time management group: +26.1 ± 36 (160, 2+0.02) |
| `TM_FALL` | 1 | Longer when the score falls | Default | same group |
| `TM_INST` | 1 | Longer when the best move changes | Default | same group |
| `TM_EFFORT` | 1 | Shorter when one root move takes ≥ 93 % of the nodes | Default | same group |
| `TM_INCD` | 0 | Do not deepen after an iteration that ended past half of the time | Closed | −24.0 ± 19.1 (580, 1+0.01) |

**Lazy SMP**

| Switch | Default | What it does | Status | Evidence |
|---|---|---|---|---|
| `SMP_VOTE` | 1 | Best move by a vote over the root results of all threads | Default | +8.9 ± 10.8 (2000, Threads 2, 40 ms) |
| `SMP_TMBMC` | 1 | Best-move-change count of time management averaged over all threads | Default | +14.3 ± 14.7 (1000, Threads 2, 1+0.01) |
| `SMP_ASPSTEP` | 2 | Helper k starts with aspiration half-width `ASP_W` + (k mod 8) · 2 | Default | thread diversity |
| `SMP_POOL` | 1 | Persistent helper threads | Speed | same search |
| `PV_FINAL` | 1 | Extra info line when the root line changed after the last one | Default | output only, no effect on the search |

**Evaluation adjustments inside the search**

| Switch | Default | What it does | Status | Evidence |
|---|---|---|---|---|
| `EV_R50` | 0 | Eval fades towards 0 with the fifty-move counter: eval · (1 − halfmove / `EV_R50C`) (E16) | Retest | screening only: −5.2 ± 39 (200, 20 ms) |
| `EV_CORR` | 0 | Pawn-structure correction history added to the eval (E17) | Retest | −13.2 ± 16.4 (1000, 20 ms); the table starts empty each game and short searches fill it poorly |

**Implementation switches** (Speed: the same tree, or for `BB` another move order with the same rules)

| Switch | Default | What it does |
|---|---|---|
| `NUNITS` | 32 | Build variable of `build/build.sh`: number of thread units, the maximum of the UCI option Threads (1 = single-thread build) |
| `BB` | 1 | Magic bitboards mirror the 0x88 board (0 = pure 0x88 engine, another move order); against the pure 0x88 engine +228 ± 59 (118, 40 ms) |
| `PREPRUNE` | 1 | Legality and check test of quiet moves from bitboards before making them |
| `SIMDPICK` | 5 | SIMD selection of the best remaining move (SSE4.1 / SSE2 chosen at start-up); same tree, +32.5 ± 13.1 (1414, 20 ms) from speed alone |
| `INCHKHINT` | 1 | The parent passes the in-check state to the child |
| `TTPREFETCH` | 1 | Prefetch of the TT bucket after a move |
| `TT_HUGE` | 1 | Transparent huge pages for the TT (Linux) |
| `DEV8SEARCH` | off | Reference search: turns the accepted search switches, continuation history, capture history and singular extension off (candidate `A0_DEV8`) |

### 1.3 Search constants

| Constant | Default | T | Used by |
|---|---|---|---|
| RFP margin per ply / improving credit | 85 / 35 | 0 / 1 | reverse futility |
| `NMP_RB` / `NMP_EDIV` | 3 / 200 | 2 / 3 | null move reduction |
| Futility base / per ply | 110 / 90 | 4 / 5 | futility pruning |
| `LMR_CUTP` | 1 | 6 | `LMR_CUT` |
| `LMR_HT` | 8000 | 7 | `LMR_HIST` threshold |
| `RAZOR_B` / `RAZOR_M` / `RAZOR_MAXD` | 130 / 250 / 3 | 8 / 9 / – | razoring margin (per d², `RAZOR_SQ` 1) and depth limit |
| `LMR_DEEPM` / `LMR_SHALM` | 60 / 8 | 10 / 11 | `LMR_RES` margins |
| TM fall per cp (search / iteration) | 24 / 12 | 12 / 13 | `TM_FALL` |
| TM stable / effort factor (/1024) | 780 / 780 | 14 / 15 | `TM_STAB`, `TM_EFFORT` |
| TM per best-move change (/1024) | 1100 | 16 | `TM_INST` |
| `EDH_M` / `NMP_VERD` / `IIR_MIND` | 4 / 14 / 6 | 17 / 18 / 19 | switches that are off |
| `DELTA_M` | 200 | 58 | delta pruning |
| `LMR_HTC` | 16000 | 59 | `CH_LMR` |
| `CH_PM` / `CH_PD` | 3000 / 3 | 60 / 61 | `CH_PRUNE` |
| `SE_M` / `SE_TTD` | 4 / 2 | 62 / 63 | singular margin per ply, TT depth slack |
| `SE_MIND` | 6 | – | minimum depth of the singular test |
| `SEE_CM` | 20 | 64 | `SEE_PC` |
| `CH_BA` / `CH_BMAX` | 150 / 1500 | 65 / 66 | continuation history bonus |
| Quiet history bonus / maximum | 16 / 1600 | 67 / 68 | history update |
| `CAP_BA` / `CAP_BMAX` | 120 / 1200 | 69 / 70 | capture history bonus |
| LMR table base / factor | 750 / 2135 | 71 / 72 | LMR table (× 1/1000, × 1/10000) |
| `ASP_W` | 20 | 73 | aspiration half-width |
| `PR_MAXD` / `RFP_MAXD` | 6 / 6 | 74 / 75 | depth limits of LMP / futility / `CH_PRUNE` and of RFP |
| `LMP_B` | 5 | 76 | late move count |
| `IIR_D` | 4 | 77 | IIR depth |
| `CAP_SEEB` / `CAP_SEESH` | −10 / 6 | 78 / 79 | `CAPHIST` 2 SEE split |
| `TMC_F0` / `TMC_F1` / `TMC_PM` / `TMC_H` / `TMC_CAP` | 140 / 260 / 60 / 40 / 10 | 80 .. 84 | `TM_CURVE` |
| `CAP_LIM` / `CAP_SH` | 8000 / 13 | – | capture history bounds and gravity |
| `SEE_MAXD` / `SEE_QM` | 6 / 64 | – | `SEE_PC` depth limit, `SEE_PQ` |
| `PC_MIND` / `PC_M` / `PC_R` | 5 / 50 / 4 | – | `PROBCUT` |
| `EV_R50C` | 200 | 30 | `EV_R50` |
| `CORR_BITS` | 14 | – | `EV_CORR` table size |

**Fixed limits** (not switches):

| Constant | Value | Meaning |
|---|---|---|
| `MAXPLY` | 128 | maximum search ply |
| `MAXGAME` | 4096 | game history in plies (repetition detection) |
| `HASHDEF` / `HASHMAX` | 32 / 32768 | default and maximum Hash in MB |
| `MOVEOH` | 12 | default Move Overhead in ms |
| `MATE` / `MATEB` | 30000 / 29800 | mate score and the bound above which a score is a mate |
| `EV_KNOWNWIN` | 10000 | base score of a known win of the special endgame evaluation |
| `EV_N` | 420 | entries of the evaluation term table `evp` |
| `CORR_LIMIT` / `CORR_BMAX` | 1024 / 256 | `EV_CORR` entry bound (128 cp) and update bound |

**Measured parameter changes:**

| Change | Result | Status |
|---|---|---|
| SPSA of RFP, null move, futility, razoring and delta margins (14,800 games) | no parameter moved beyond the noise | defaults kept |
| `SE_M` 6 | −17.0 ± 14.6 (900) | Closed |
| `SE_MIND` 8 (`X11_MIND8`) | −10.8 ± 18.5 (450) | Closed |
| `SE_TTD` 3 (`X10_TTD3`) | 2 was chosen over 3: +10.8 ± 21.3 (450) | Retest at a longer time control |
| `TMC_F0` / `TMC_F1` 160 / 280 (`X13_TMCMORE`, 10 % more time) | +7.9 ± 18.2 (530) | Borderline |
| Global eval scale × 1.10 / × 0.90 | −29.6 ± 22.4 / −32.2 ± 20.6 (400 each) | Rule: see 2.5 |

The search margins were tuned for the current eval scale; see the rule in section 2.5.

### 1.4 Search experiments removed from the code

Three groups of search changes were implemented, each part behind its own switch. With every switch at 0 the build was
byte-identical to the default. Every part was measured and then removed from the source, so these ideas have no switch
today.

The tables use these measures:

- **Nodes:** total nodes at depth 12 on 40 positions, against the default.
- **Screen:** 300 games at 3+0.03 (about ±25 Elo).
- **Long run:** 2 × 1000 games at 3+0.03 on new seeds; the number of games is given where a run was stopped earlier.

Status rules for these parts: **Closed** = a negative result proven in at least 1,000 games (the whole 95 % interval below
0); **Retest** = a positive trend in a long run, waiting for an SPRT at a longer time control; **Low priority** = no proof
either way.

The batches are numbered by their place in the search plan; batches 3 and 4 were never implemented.

**Batch 1: late move reductions in fractional plies.** The aim was finer reductions: R in 1/1024 ply from a continuous
formula, adjusted by statistics of the move and the node, instead of the integer table and integer steps.

| Part | What it did | Nodes | Screen | Long run | Status |
|---|---|---|---|---|---|
| Continuous formula | R = 768 + 1024 · 0.2135 · log2 d · log2 m; halved when not improving; a term for alpha − eval; more reduction at all nodes (R += R / (d + 1)); a negative R searches one ply deeper | −8.5 % | +15.1, −19.5 on a second seed (about −7 in 800) | −10.4 ± 10.3 | Closed: reduces more than the integer table |
| TT-PV term | −1 ply at nodes that were on a PV, −0.5 more each for ttScore > alpha and ttDepth ≥ depth (a PV bit in the TT entry) | +24 % with the formula | +20.9, +2.3 (about +12 in 600) | −11.1 ± 9.9 | Closed |
| Statistic score | History-based score: 2 × butterfly history plus both continuation rows (captures: victim value and capture history), divided by 15.6 | +3 % with the formula | +19.7, +3.5 (about +12 in 600) | −3.5 | Low priority |
| Formula + TT-PV + statistic score | The full subset | +31 % | +10.4, −9.3 (about 0 in 600) | −4.2 | Low priority |
| Cutoff count | More reduction when the child already had several cutoffs | −14 % (term alone) | −6.9 alone, about −35 inside the formula | – | Low priority (screen only) |
| Reduction from move 2 | Moves 2 and 3 also reduced | – | −22.0 | – | Low priority (screen only) |
| Captures and checks reduced | Late captures and checking moves reduced | – | −11.6 | – | Low priority (screen only; `CAP_LMR` −1.4, `LMR_BADCAP` −5.6 in 500 games) |
| Prior reduction, continuation bonus after a re-search | The parent's reduction raises the child's; a fail-high after LMR rewards the move | – | −8.1 | – | Low priority (screen only) |
| Reduced child as cut node | The reduced search treats the child as an expected cut node | – | +8.1 | – | Low priority (screen only) |

The overall level of reduction decided the results, not any single term. Terms that gave reductions back beat the formula
alone, but none beat the integer default.

**Batch 2: history feedback.** The aim was to feed more search events into the history tables.

| Part | What it did | Nodes | Screen | Long run | Status |
|---|---|---|---|---|---|
| TT-cutoff update | On a TT cutoff, a bonus for the quiet TT move and a malus for the parent's early quiet move | +1.9 % | +33.7, +10.4 (about +22 in 600) | +7.5 | Retest |
| Exact-node update | PV nodes update the histories as a cutoff does | −5.9 % | +17.4 | +3.0 | Low priority |
| Refuted parent move | Continuation malus for the parent's first move when it was refuted early | +5.2 % | +11.6 | +11.8 ± 10.0 (LOS 99 %); +10.4 ± 12.5 on a third seed | Retest: failed the 10+0.1 SPRT with the current thresholds; an SPSA of `CH_PM`, `CH_PD`, `LMR_HT` with the malus moved them by less than one step (8,200 games) |
| Continuation rows 3 and 4 plies back | Two more continuation rows in ordering and update | −0.2 % | +8.1 | – | Low priority (screen only) |
| Fail-low bonus | A node that fails low rewards the parent's quiet move | +5.1 % | −4.6 | – | Low priority (screen only) |
| TT-cutoff + exact + refuted | Three parts together | – | – | −12.5 | Closed |
| The same + rows 3 and 4 | Four parts together | +5.6 % | −20.9, −10.4 | −31.2 | Closed |
| Smaller maluses | TT-cutoff malus 500, exact nodes bonus only, refuted malus × 0.5 | – | – | +10.8 ± 8.1 (3000) | Retest: the strongest long-run result of the batches |

The parts win alone and lose together: the combinations of three and four parts are proven negative. `HSTAT` showed why: the extra maluses push the history values below the fixed
thresholds that read them. `CH_PRUNE` dropped 1.7 % of its tested moves in the default, 16.5 % with the refuted-move malus
and 52 % with three parts together. A change of the history scoring needs a retune of `CH_PM`, `CH_PD` and `LMR_HT`.

**Batch 5: quiescence search.**

| Part | What it did | Nodes | Screen | Long run | Status |
|---|---|---|---|---|---|
| TT stand pat | Stand pat from the TT score when its bound allows it | +2.9 % | +1.2 | +2.4 ± 16.5 (710) | Low priority |
| Fail-high averaging | A fail high returns (value + beta) / 2 | +16.7 % | +12.7 | −6.7 ± 19.9 (520) | Low priority |
| SEE threshold | Captures with SEE ≥ −36 cp are searched (instead of ≥ 0) | +7.7 % | +11.6 | +4.9 ± 12.7 (1000) | Retest |
| Capture futility | For captures that are no recapture and no promotion, with base = static eval + 150: skip from the third such capture on, when base + victim ≤ alpha, or when SEE < alpha − base; a capture that gives check is searched | −3.6 % | +11.6 | −7.7 ± 15.1 (770) | Low priority |
| Refuted-move malus + SEE threshold + TT stand pat | The three parts together | +6.5 % | – | +8.6 ± 12.0 (1300, LOS 92 %) | Retest: the strongest result of the quiescence batch |

**What the three batches showed about measuring:**

- **Screens only find clear failures.** Over 25 screens the spread was 16.7 Elo, against 12.5 for noise alone.
- **Expect a winner by selection.** The best of several screens lands near +20 .. +30 even without a real gain, and a
  combination of the "best" parts regresses towards zero.
- **Decide on long runs only.** A default needs 2 × 1000 games and then an SPRT at 10+0.1.

---

## 2. Evaluation

### 2.1 Structure

```
mg, eg  = material + PST (incremental) + bishop pair + terms below        (White's view, both sides by the same code on a mirrored board)
e       = trunc((64 · mg · ph + sf · eg · (24 − ph)) / 1536)              ph = game phase 0 .. 24, sf = endgame scale factor 0 .. 64
eval    = ±e + TEMPO                                                       (special endgames bypass this)
```

Every term with parameters is linear in them, so `tuner/` tunes every table; the scale factor, the special endgames and the switches marked as non-linear below are outside that model. `tuner/check_eval.py` proves that the Python model
matches the engine bit for bit. Material, PST, the `evp` term table, the bishop pair (`BP_MG` / `BP_EG` 17 / 68) and `TEMPO` (18) are tuned values.

**Always on with `EVAL_FULL` 1** (`E0_PSTEVAL` = material + PST + bishop pair + tempo only):

| Term | Definition |
|---|---|
| Mobility | Attacked squares per piece type (N, B, R, Q) inside the mobility area; one value per count |
| Pawn structure | Doubled, isolated, supported, phalanx |
| Passed pawns | Value per rank; own and enemy king distance to the stop square |
| Rooks | Open file, semi-open file |
| King shield | Own pawns one and two ranks in front of the king on its three files (replaced by `EV_SHELTER` when on) |
| King attack | Attack units on the king zone, table indexed by the sum (replaced by `EV_KDANGER`, which is on) |
| Pawn threats | Own pawn attacks an enemy piece |
| Knight outpost | Ranks 4 .. 6, pawn-supported, safe from enemy pawns |

### 2.2 Evaluation switches

| ID | Switch | Concept | Default | Status | Evidence |
|---|---|---|---|---|---|
| E1 | `EV_ENDG` | Specific endgames: bare-king mating (KQK, KRK, KBNK), KPK bitbase, insufficient material and wrong-bishop draws | 1 | Default | with E2, the second rule sets and a joint retune: endgame set +14.3 ± 10.7 (534), normal openings +29.3 ± 16.6 (1000); alone with the old tables +0.7 ± 15.9 (970, 20 ms) |
| – | `EV_ENDG2` | Special values for pawnless KQ-KR, KR-KB, KR-KN, fitted to tablebase verdicts | = `EV_ENDG` | Default | same test; ablation `X4_NOENDG2` |
| E2 | `EV_SCALE` | Endgame scale factor on the eg part: drawish material, opposite-coloured bishops, rook endings on one flank, lone queen, few pawns | 1 | Default | same test; with the first rule set alone +1.0 ± 15.5 (1000, 20 ms); ablation `X2_NOSCALE` |
| – | `EV_SCALE2` | Second rule set with fitted constants and class rules for drawn single-pawn endings | = `EV_SCALE` | Default | ablation `X3_SCALEV1` |
| – | `EV_SCALE6` | Rule 6 of the first rule set (few pawns) | 1 | Diagnostic | acts only with `EV_SCALE2` 0 |
| E4 | `EV_LAZY` | Lazy evaluation: return material + PST beyond `EV_LAZYT` (1000), skip the second pass beyond `EV_LAZYT2` (700) | 0 | Retest | screening only: −15.6 ± 34 (200, 20 ms, untuned tables) |
| E5 | `EV_THREAT2` | Threats: by minors, rooks and king, hanging pieces, safe pawn and pawn-push threats, restricted squares, threats on the queen; sub-switches `EV_THRMIN`, `EV_THRHANG`, `EV_THRPAWN`, `EV_THRRESTR`, `EV_THRQUEEN` | 1 | Default | +53.6 ± 24.7 (418, 20 ms); ablation `X5_NOTHREATS` |
| E6 | `EV_PINS` | Pinned pieces and a mobility area without the king, queens, pinned pieces, blocked or low pawns and squares attacked by enemy pawns; own mobility tables | 1 | Default | +27.2 ± 23.8 (500, 20 ms); ablation `X6_NOPINS` |
| E7 | `EV_KDANGER` | King danger: attacker weights, weak ring squares, safe checks per piece, pins, defending knight → 64-entry table | 1 | Default | +30.0 ± 24.2 (500, 20 ms); ablation `X7_NOKDANGER` |
| E8 | `EV_PASSED2` | Passed pawns, second pass: file, king proximity weighted by rank, free and defended path, rook or queen behind | 1 | Default | +25.1 ± 22.7 (500, 20 ms); ablation `X8_NOPASSED2` |
| E9 | `EV_PAWNS2` | Backward, weak unopposed, weak lever, connected per rank, blocked on rank 5 / 6 | 0 | Closed | −9.0 ± 22.0 (500, 20 ms, tuned) |
| E10 | `EV_SHELTER` | King shelter / pawn storm tables on three files (best of the king square and the castled squares), king–pawn distance | 0 | Closed | −2.1 ± 21.9 (500); −7.6 ± 16.0 (1000), 20 ms, tuned; 4 % slower |
| E11 | `EV_PIECES2` | Piece terms: `EV_PBISH` (bishop pawns, long diagonal, bishop outpost), `EV_PMINOR` (reachable outpost, minor behind pawn, king protectors), `EV_PROOK` (closed file, trapped rook, 7th rank, connected rooks, rook behind passer), `EV_PQUEEN` (weak queen) | 0 | Borderline | +13.2 ± 23.3 (500); −7.3 ± 16.4 (1000), 20 ms, tuned; 10–14 % slower; sub-switches not tested alone |
| E12 | `EV_IMBAL` | Quadratic material imbalance (ours × ours, ours × theirs) | 0 | Closed | −28.6 ± 23.6 (500, 20 ms); tuned from zero −23.2 ± 18.8 (524, 3+0.03) |
| E13 | `EV_SPACE` | Space: safe central squares behind own pawns, weighted by piece count (only with non-pawn material ≥ `EV_SPACENPM`) | 0 | Closed | −0.7 ± 16.2 (1000, 20 ms); no information in the tuning data |
| E14 | `EV_INIT` | Initiative / complexity: passed pawns, pawn count, outflanking, infiltration, pawns on both flanks, no pieces, almost unwinnable; corrects eg towards or away from 0 | 0 | Borderline | −1.5 ± 14.1 (900, 3+0.03) with fitted constants |
| E15 | `EV_GRAIN` | Output grain: the score truncated to a multiple of `EV_GRAINS` (8) | 0 | Retest | screening only: +6.9 ± 38 (200, 20 ms) |
| E16 | `EV_R50` | Fifty-move fade (search side, section 1.2) | 0 | Retest | screening only |
| E17 | `EV_CORR` | Pawn correction history (search side, section 1.2) | 0 | Retest | −13.2 ± 16.4 (1000, 20 ms) |
| E20 | `EV_KLINES` | Open lines against the enemy king: open files, storming pawns, rooks and queens on files without a shelter pawn (mg only) | 1 | Default | +6.6 ± 14.0 (1000, 3+0.03); tactics neutral; ablation `X16_NOKLINES` |

The other numbers are not evaluation switches:

- **E3** was the combination E1 + E2.
- **E18 and E19** are `CAPHIST` 1 and 2 (section 1.2).
- **E21** was a joint retune of all tables: −3.8 ± 14.4 (1000, 3+0.03), and +0.3 ± 14.4 when scaled back to the default eval scale. Closed.

### 2.3 Infrastructure switches

| Switch | Default | What it does | Status |
|---|---|---|---|
| `EVAL_FULL` | 1 | Full evaluation (0 = material + PST, forced with `BB` 0); against material + PST +119.1 ± 29.2 (400, 40 ms) | Default |
| `HWPOPCNT` | 2 | POPCNT or plain code chosen at start-up (0 plain, 1 POPCNT only) | Speed (`S1_SWPOPCNT`) |
| `EVCACHE` | 1 | Static eval stored in the TT entry | Speed (`S0_NOEVCACHE`) |
| `EV_PHASH` | 0 | Pawn cache per thread (2^`EV_PHBITS` = 2^13 entries, 512 KiB); same values | Speed, not yet measured on the target machine (`S2_PHASH`) |
| `EV_ATTMAPS` | auto | Attack maps of both sides and the second evaluation pass; forced on by every second-pass term | – |
| `EV_TUNEMODE` | 0 | Turns off every non-linear part (lazy, initiative, grain) for tuning builds | Diagnostic |
| `EV_OSCALE` | 1024 | Scales the normal evaluation by N/1024 (test builds) | Diagnostic |

The console command `evalcfg` prints the evaluation switches of a build as a bit mask (`EV_CFG` in `src/eval.asm`; the bit
order is `FLAG_BITS` in `tuner/model.py`).

### 2.4 Constants outside the linear model

These are not tuned by `tuner/tune.py`:

| Group | Constants | Fitted by | T |
|---|---|---|---|
| King danger weights (`EV_KDANGER`) | `KD_WEAK` 2, `KD_QCHK` 6, `KD_RCHK` 5, `KD_BCHK` 3, `KD_NCHK` 4, `KD_UNSAFE` 1, `KD_PIN` 2, `KD_KNDEF` 3 | hand-set; SPSA target | 20 .. 27 |
| Lazy thresholds (`EV_LAZY`) | `EV_LAZYT` 1000, `EV_LAZYT2` 700 | hand-set | 28, 29 |
| Initiative (`EV_INIT`) | `IN_PASSED` .. `IN_MGADD` (9 constants) | `tuner/fit_init.py` | 31 .. 39 |
| Scale rules (`EV_SCALE2`) | `SC_*` (18 constants) | `tuner/fit_scale.py`; SPSA did not move them | 40 .. 57 |
| Special endgames (`EV_ENDG2`) | `EG2_*` (7 constants) | `tuner/fit_special.py` | – |

### 2.5 Rules for evaluation work

- **Tune before testing.** A term with parameters is tuned before its SPRT; an SPRT of hand-set values tests the values,
  not the idea.
- **Keep the eval scale.** Every retune or new term is scaled to the mean |eval| of the default (`tune.py --norm-scale asm`,
  or `EV_OSCALE`) before it is measured. A ±10 % eval scale costs about 30 Elo by itself, because the search margins are
  tuned for one scale.
- **Disabled code stays.** A rejected switch stays in the source with its tuned values, so that its candidate is the tested
  build.
- **Making a switch the default.** Change its default in the header, rebuild, run `tests/t_eval.py` and
  `tuner/check_eval.py`, and re-record `tests/golden_depth8.json`. Its candidate then becomes an ablation (`-DNAME=0`).

---

## 3. Test and diagnostic builds

These flags are never set in a release build. They are set with `-DNAME` and mostly built with
`sh tests/build1.sh OUT -DNAME`. The multi-unit test builds come from `tools/build_testbuilds.sh`.

**Statistics:** a console command prints counters of the main thread.

| Flag | Command | What it counts | Used by |
|---|---|---|---|
| `ORDSTAT` | `ordstat` | Move ordering: first-move cutoff rates, cutoff move class (TT move, capture, killer, quiet), rank of the cutoff move, captures before a quiet cutoff | `tests/t_ordering.py` |
| `HSTAT` | `hstat` | History pruning: `CH_PRUNE` tests and prunes with the continuation score by class, `LMR_HIST` steps with the history by class | manual |
| `SESTAT` | `sestat` | Singular extension: tests, singular, multi-cut, neither, by depth | `tests/t_singular.py` |
| `PCVERIFY` | `pcstat` | ProbCut: tries, captures searched, would-be cuts, and how many the normal search refutes (no cut is made) | manual |
| `EVALPROF` | `evalprof` | Eval calls, rdtsc cycles, pawn cache probes and hits | `tests/t_phash.py` |
| `CHSHTEST` | `chsum` | Entries of every unit's continuation history table (`CH_SHARED`) | `tests/t_chshared.py` |
| `TMCDBG` | `info string tmc ...` | Soft and hard time limit of every clock-based `go` | `tests/t_tmcurve.py` |
| `VOTETEST` | `info string vote ...` | Candidates and winner of the Lazy SMP vote | `tests/t_smp_vote.py` |
| `SEETEST` | `see MOVE THRESHOLD` | SEE of a single move against a threshold | `tests/t_see.py` |

**Verification:** a second computation is compared with the fast path, and the engine exits with an error code on a
difference.

| Flag | What it checks | Used by |
|---|---|---|
| `PPVERIFY` | Pre-make legality and check test (`PREPRUNE`) against make / unmake | `tests/t_ppverify.py` |
| `PICKVERIFY` | SIMD move pick against the scalar loop (exit 6) | with `PPVERIFY` |
| `INCHKVERIFY` | In-check hint against a fresh in-check test (exit 4 / 5) | with `PPVERIFY` |
| `CUTVERIFY` | cutNode is 0 / 1 and never set at a PV node (exit 7) | manual |
| `SEVERIFY` | Exclusion search never in check, never makes the excluded move, never stores, never searches another position (exit 8 .. 11) | `tests/t_singular.py` |
| `FORCE_SSE2` | Forces the SSE2 copy of the move pick | manual |

**Test harness builds:**

| Flag | What it does | Used by |
|---|---|---|
| `UNITTEST` | Single-thread engine with a UCI option `Unit`: the search runs in thread unit k | `tests/t_units.py`, `t_units_conc.py`, `verify_units.bat` |
| `UNITBREAK` | A unit that does not receive the position, to prove that `t_units.py` fails | `tests/t_units.py` |
| `SMPTEST` | Helpers run to their own end without stop, `CH_SHARED` forced off, unit reports | `tests/t_smp_equiv.py`, `verify_smp.bat` |
| `NOTT` | Transposition table off: concurrent searches become deterministic | `t_units_conc.py`, `t_smp_equiv.py` |
| `TTPLAIN` | Plain (not lock-free) TT stores, the variant that must fail the race test | `tests/t_ttrace.py` |
| `TTRACE` | A pause between the two TT stores to provoke races | `tests/t_ttrace.py` |
| `TTFUZZ` | TT probes return random moves | `tests/t_ttfuzz.py` |
| `TUNE` | Constants read from the options `T0` .. `T84` (sections 1.3, 2.4) | `tools/spsa.py` |
| `NOSMP` | Single-thread engine on either OS | manual |
| `WIN` | Windows target (set by the build scripts) | `build/` |


### 3.1 Using them for bug hunting

The counter builds count in the main thread only. With Threads 1 and a fixed depth the search is deterministic, so two
builds can be compared number by number, without games and without error bars. Compare the node counts first: a change
of the tree moves every share, even where no decision changed. The meaning of every counter is listed at its definition
in `src/p4_search.asm` (`ORDSTAT`, `HSTAT`, `SESTAT`, `PCVERIFY`).

```sh
sh tests/build1.sh /tmp/z_hstat -DHSTAT                       # the same for the other counter builds
printf 'uci\nposition startpos\ngo depth 12\nisready\nhstat\nquit\n' | /tmp/z_hstat
```

Each counter command prints one line after the search (`hstat`, `ordstat`, `sestat`, `pcstat`, `evalprof`). The counters
add up over all searches of one process and are not reset by `ucinewgame`: start a fresh process for each measurement, or
subtract two readings.

**By the part of the engine that changed:**

| Change | Run | What to look at |
|---|---|---|
| Move ordering: history, killers, continuation or capture history, SEE ordering | `ORDSTAT` (`tests/t_ordering.py OLD --ab NEW`, or `--ref tests/ordering_ref_*.json`) | first-move cutoff rate per depth class and node type, TT move picked first, kind and rank of the cutoff move |
| History bonus, malus or gravity; thresholds that read the history (`CH_PRUNE`, `LMR_HIST`) | `HSTAT` | how the history values are spread around the fixed thresholds: how many moves `CH_PRUNE` drops and how many LMR steps fire. A history change that shifts these distributions also changes pruning and LMR. |
| Singular extension: margin, depth limits, multi-cut, negative extension | `SESTAT` + `SEVERIFY` (`tests/t_singular.py`) | singular / multi-cut / neither shares and the nodes of the exclusion searches per depth; any `SEVERIFY` exit is a bug |
| ProbCut margin or depth | `PCVERIFY` with `-DPROBCUT=1` | would-be cuts that the full search refutes (tactical risk) and nodes saved |
| Late-move pruning, futility, SEE pruning of quiet moves, bitboard move generation | `PPVERIFY` + `PICKVERIFY` + `INCHKVERIFY` (`tests/t_ppverify.py`, also `--selftest`) | any exit code: the pre-make test, the SIMD pick or the in-check hint disagrees with the slow path |
| `score_moves` or the move pick | `PICKVERIFY` | exit code 6 |
| Passing of the in-check state (null move, evasions, quiescence) | `INCHKVERIFY` | exit codes 4 / 5 |
| cutNode logic (`LMR_CUT`, `NMP_CUT`, `TTCUT_COND`, IIR, ProbCut) | `CUTVERIFY` | exit code 7 |
| `see_ge`, piece values, x-rays | `SEETEST` (`tests/t_see.py`) | mismatches against the reference exchange search |
| Transposition table layout, store or probe | `TTPLAIN` / `TTRACE` (`tests/t_ttrace.py`), `TTFUZZ` (`tests/t_ttfuzz.py`) | torn entries, or an illegal TT move played or sent as ponder |
| A new per-thread variable, the thread units or Lazy SMP | `UNITTEST`, `NOTT` (`tests/t_units.py`, `t_units_conc.py`), `SMPTEST` (`tests/t_smp_equiv.py`), `tests/t_smp_audit.py` | a unit that searches differently from unit 0, a helper that writes shared state |
| Best-move vote | `VOTETEST` (`tests/t_smp_vote.py`) | the engine's choice against the written rules |
| Shared continuation history | `CHSHTEST` (`tests/t_chshared.py`) | which tables the helpers fill |
| Time allocation formula | `TMCDBG` (`tests/t_tmcurve.py`) | soft and hard limit against the model |
| Evaluation terms, attack maps, pawn cache | `EVALPROF` (`tests/t_phash.py` for the cache) | rdtsc cycles per eval call A/B (the cost of a term), pawn cache hit rate, eval calls saved by `EVCACHE`. The cycle counts are noisy on a shared or virtual machine: repeat the run, and measure speed with `tests/bench_ab.py`. |

Evaluation values are checked without a special build: `tuner/check_eval.py` (engine against the Python model) and
`tests/t_eval.py` (colour symmetry, incremental against full evaluation). Run both after any change in `src/eval.asm`.

**By symptom:**

| Symptom | First check |
|---|---|
| Fixed-depth node counts changed although the change should not touch the tree | `tests/t_golden.py`, then the verification build of the touched code |
| Crash, illegal move or illegal ponder move | `PPVERIFY` + `PICKVERIFY` + `INCHKVERIFY`, `TTFUZZ`, `tests/t_memsafety.py` |
| Elo loss after a history or ordering change | `ORDSTAT` A/B, then `HSTAT` |
| Tactics loss after an extension or pruning change | `SESTAT` (extensions), `PCVERIFY` (ProbCut), then `./tactics.sh ab` |
| Lower nps after an evaluation change | `EVALPROF` cycles per call, `tests/bench_ab.py` |
| Results differ with Threads > 1 or between thread units | `tests/t_units_conc.py`, `SMPTEST`, `tests/t_smp_audit.py` |
| Time losses or moves that use too much of the clock | `TMCDBG`, `tests/t_tmclock.py` |
| Eval differs between engine and tuner, or by colour | `tuner/check_eval.py`, `tests/t_eval.py` |
---

## 4. Open work

**Retest at a longer time control**, 10+0.1 or longer, at least 2 × 1000 games:

- E4 `EV_LAZY`
- E15 `EV_GRAIN`
- E16 `EV_R50`
- E17 `EV_CORR`
- `CH_SHARED` with 8 or more threads
- `SE_TTD` 3

All of these have only short time control or screening results.

**Search experiments to retest** (section 1.4), each as a switch again, 2 × 1000 games and then an SPRT at 10+0.1 or longer:

- the smaller history maluses (+10.8 ± 8.1 in 3000 games);
- the refuted-parent-move malus (+11.8 ± 10.0 in 2000 games; failed the 10+0.1 SPRT);
- the TT-cutoff history update (+7.5 in 2000 games);
- the refuted-move malus together with the quiescence SEE threshold of −36 cp and the TT stand pat (+8.6 ± 12.0 in 1300
  games), and the SEE threshold alone (+4.9 ± 12.7 in 1000 games).

**Borderline, for package tests or a retune:**

- E11 `EV_PIECES2`: a faster implementation, or its cheapest sub-switch alone.
- E14 `EV_INIT`.
- `LMP_SF`, together with wider pruning depths.
- `X13_TMCMORE`.

**Evaluation ideas with a signal in the tuning data but no term or test yet:**

- passed pawns on rank 5 and beyond in the middlegame (+24 cp mg in a data fit);
- protected, connected and blockaded passers, and a rook behind a passer (8 .. 14 cp, small).

**Missing endgame knowledge:**

- value functions for KRKP, KRKB, KRKN with pawns, and KQKP;
- scale rules for KBPP-KB and KRPP-KRP;
- tablebase probing.

**Search ideas not yet tried:**

- a narrower check extension (today every node in check is extended);
- SEE and delta piece values aligned with the evaluation's material values;
- an easy-move rule at the root;
- adaptive aspiration windows;
- counting a repetition before the root as a draw only at the third occurrence;
- a mate check at the 100th half-move.

**Search constants for SPSA:**

- `CH_PM`, `CH_PD` and `LMR_HT`: the history counters show that continuation history pruning drops only 1.7 % of the tested
  moves;
- the `KD_*` weights.

Give SPSA few parameters with a clear effect, and verify every result with an SPRT.
