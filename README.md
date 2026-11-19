# Zeval

A UCI chess engine written entirely in x86-64 assembly (NASM), with a classical tapered evaluation. In loving memory of my father.

Zeval is a self-contained engine: no C library, no C runtime, no external dependencies. The Linux build is a static ELF that talks to the kernel through raw system calls; the Windows build is a single `zeval.exe` that imports nothing but `KERNEL32.dll`. Both are built from the same source and play the same moves: a fixed-depth search gives identical node counts on both systems.

## Project Structure

The repository holds the engine and everything used to build, test and tune it:

- **Engine** (`src/`): the assembly source. Move generation, search, evaluation, UCI, the Windows platform layer and the thread code.
- **Build** (`build/`): `build.sh` (Linux, also cross-builds the Windows executable) and `build.bat` (Windows).
- **Tests** (`tests/`): Python test scripts. Fixed-depth regression, perft, UCI conformance, time management, Lazy SMP audits, a self-play match runner with SPRT.
- **Tactics** (`tactics/`): a tactical test suite of 1,146 positions with a runner, an A/B analysis and recorded reference results.
- **Tuner** (`tuner/`): Texel-style tuning of the evaluation in Python, with a bit-exact Python model of the engine's evaluation.
- **Tools** (`tools/`): magic bitboard and KPK bitbase generators, SPSA tuning of search parameters, candidate builds, script lint.

## Architecture Highlights

### Search

Principal Variation Search with iterative deepening and aspiration windows.

- **Transposition table:** lock-free (key XOR data), two-entry buckets, aging, mate-score aware, also used in quiescence. The static evaluation is cached in the table entry. On Linux the table is backed by transparent huge pages.
- **Pruning and reductions:** null-move pruning, razoring, reverse futility pruning, futility pruning (on the LMR depth), late-move pruning, SEE pruning of captures and quiet moves, internal iterative reduction, late move reductions with history, cut-node and improving adjustments.
- **Extensions:** check extension and singular extension of the TT move (with multi-cut).
- **Move ordering:** TT move, captures by MVV-LVA split into good and bad by static exchange evaluation (bad captures after the quiet moves), killer moves, history with malus, continuation history (one and two plies back).
- **Quiescence:** captures and promotions, check evasions, SEE and delta pruning.
- **Pre-make pruning:** legality and "gives check" of a quiet move are decided from the bitboards before making it, so pruned moves are never made and unmade.

### Parallelism

Lazy SMP with up to 32 threads.

- **Thread units:** the search code is assembled once per thread, and each copy has its own state block. No thread-local storage or OS thread API is involved in reaching per-thread state.
- **Thread pool:** helper threads are created once and wait between searches without using CPU time.
- **Shared state:** the threads share only the transposition table. Helpers use staggered aspiration windows.
- **Best move:** chosen by a vote over the root results of all threads.

### Time Management

- **Allocation:** the time per move follows the game: a share of the remaining clock plus the expected increments, rising over the first moves of the game.
- **Adjustment per move:** the stop time is shortened when the best move is stable or one root move takes most of the nodes. It is lengthened when the best move changes or the score falls.
- **Safety:** a hard limit per move and a configurable Move Overhead.

### Evaluation

A classical evaluation, tapered between middlegame and endgame:

- **Base:** material and piece-square tables (incremental), mobility over a pin-aware mobility area, bishop pair, tempo.
- **Pawn structure:** doubled, isolated, supported and phalanx pawns.
- **Passed pawns:** rank, king distances, file, free and defended path.
- **Pieces:** rooks on open and semi-open files, knight outposts.
- **King safety:** king shield and king danger (attackers, weak ring squares, safe checks, pins).
- **Threats:** pawn threats, hanging pieces, restricted squares, threats against the queen.
- **Endgame scale factor:** drawish material, opposite-coloured bishops, wrong bishop, typical drawn single-pawn endings, rook endings, few pawns.
- **Special endgame knowledge:** mating patterns against a bare king (including KBNK), a KPK bitbase built into the binary, KQ-KR, KR-KB and KR-KN.

Every term is linear in its parameters. The values were tuned on about 4 million quiet positions; `tuner/check_eval.py` proves that the Python model reproduces the engine's evaluation exactly. The evaluation picks a POPCNT or a plain x86-64 code path at start-up.

## Building the Engine

### Prerequisites

- **Linux, release build:** NASM 2.16 or newer, GNU `ld`, and LLVM (`lld-link`, `llvm-dlltool`, `llvm-rc`) for the Windows executable.
- **Windows:** NASM and the Visual Studio Build Tools (an "x64 Native Tools Command Prompt").
- **Optional, for tests, tactics and tuning:** Python 3.8 or newer with `python-chess`, plus `numpy` and `scipy` for the tuner.

### Linux

```sh
sudo apt install nasm lld llvm
sh build/build.sh
```

This produces `dist/zeval` (Linux) and `dist/zeval.exe` (Windows, cross-built), each with 32 thread units.

A quick single-threaded build for experiments:

```sh
sh tests/build1.sh ./zeval
```

### Windows

Open an "x64 Native Tools Command Prompt" with `nasm.exe` on the `PATH`:

```bat
build\build.bat
```

The executable is `dist\zeval.exe`.

### Build Switches

Search and evaluation features are build-time switches, documented in the headers of `src/p1_data.asm` and `src/eval.asm`:

```sh
NASMFLAGS="-DSING=0" sh build/build.sh          # for example: without the singular extension
```

## Usage

Zeval speaks the Universal Chess Interface and runs in any UCI GUI or tournament manager (Arena, Cute Chess, fastchess, cutechess-cli, Lucas Chess, python-chess and others).

### UCI Options

| Option | Default | Range | Description |
|---|---|---|---|
| `Hash` | 32 | 1 to 32768 | transposition table size in MB |
| `Clear Hash` | | button | empties the transposition table and the history tables |
| `Threads` | 1 | 1 to 32 | search threads (Lazy SMP) |
| `Move Overhead` | 12 | 0 to 5000 | time in ms reserved for GUI and network latency |

### Supported UCI Commands

- `uci`, `isready`, `ucinewgame`, `setoption name <name> value <value>`
- `position [startpos | fen <fen>] moves <moves>`
- `go` with `depth`, `nodes`, `movetime`, `wtime` / `btime` / `winc` / `binc` / `movestogo`, `infinite`, `searchmoves`
- `stop`, `quit`

The output has the full principal variation, `seldepth`, `hashfull`, `currmove` lines during long searches, and `bestmove ... ponder ...`.

### Console Mode

Started without a GUI, Zeval plays in the terminal:

```sh
./zeval
```

Type moves like `e2e4` (promotion: `e7e8q`). Other commands:

- `go`: the engine moves.
- `new`: a new game.
- `time <ms>`: the time per move.
- `d`: shows the board.
- `perft <n>`: move-generation count.
- `eval`: the static evaluation.
- `quit`: exits.

Typing `uci` switches to UCI mode.

### Requirements

An x86-64 CPU (SSE2; POPCNT and SSE4.1 are used when present) running Linux or 64-bit Windows 10 / 11.

## Testing

```sh
python3 tests/t_golden.py ./dist/zeval     # fixed-depth search == the recorded reference
python3 tests/t_perft.py                   # move generation against python-chess
python3 tests/t_uci.py                     # UCI conformance
./tactics.sh smoke                         # tactical smoke test (Windows: tactics.bat)
```

On Windows, `verify.bat` checks a build against the recorded reference.

`tests/match.py` plays self-play matches with paired openings and a pentanomial SPRT; changes to the search and the evaluation are decided by such matches.

## Limitations

- **Not supported:** endgame tablebases, MultiPV, a `Ponder` option (`go ponder` is a normal timed search) and opening books.
- **Determinism:** a search with `Threads` above 1 is not deterministic; with one thread a fixed-depth search is.

## License

This project is licensed under the GNU General Public License v3.0. See the [LICENSE](LICENSE) file for details.

## Credits

See [CREDITS.md](CREDITS.md).
