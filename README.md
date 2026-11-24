<div align="center">
<img src="docs/zeval-logo.svg" alt="Zeval logo" width="150" height="150">

# Zeval
 
<a href="https://lichess.org/@/Zeval_Bot"><img src="https://img.shields.io/badge/Lichess-@Zeval__Bot-black?style=for-the-badge&logo=lichess&logoColor=white" alt="Lichess Bot"></a>
 
<a href="https://lichess.org/@/Zeval_Bot/perf/bullet"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Flichess.org%2Fapi%2Fuser%2FZeval_Bot&query=%24.perfs.bullet.rating&label=Bullet&color=D55E00&style=for-the-badge&logo=lichess&logoColor=white" alt="Bullet Rating"></a>
<a href="https://lichess.org/@/Zeval_Bot/perf/blitz"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Flichess.org%2Fapi%2Fuser%2FZeval_Bot&query=%24.perfs.blitz.rating&label=Blitz&color=E69F00&style=for-the-badge&logo=lichess&logoColor=white" alt="Blitz Rating"></a>
<a href="https://lichess.org/@/Zeval_Bot/perf/rapid"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Flichess.org%2Fapi%2Fuser%2FZeval_Bot&query=%24.perfs.rapid.rating&label=Rapid&color=009E73&style=for-the-badge&logo=lichess&logoColor=white" alt="Rapid Rating"></a>
<br>
<a href="https://lichess.org/@/Zeval_Bot/all"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Flichess.org%2Fapi%2Fuser%2FZeval_Bot&query=%24.count.all&label=Total%20Games&color=0072B2&style=for-the-badge" alt="Total Games"></a>
<a href="https://lichess.org/@/Zeval_Bot/all"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Flichess.org%2Fapi%2Fuser%2FZeval_Bot&query=%24.count.win&label=Wins&color=009E73&style=for-the-badge" alt="Wins"></a>
<a href="https://lichess.org/@/Zeval_Bot/all"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Flichess.org%2Fapi%2Fuser%2FZeval_Bot&query=%24.count.draw&label=Draws&color=999999&style=for-the-badge" alt="Draws"></a>
<a href="https://lichess.org/@/Zeval_Bot/all"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Flichess.org%2Fapi%2Fuser%2FZeval_Bot&query=%24.count.loss&label=Losses&color=D55E00&style=for-the-badge" alt="Losses"></a>
 
[English](#english) · [Türkçe](#türkçe)
 
</div>

---

## English

A UCI chess engine written entirely in x86-64 assembly (NASM), with a classical tapered evaluation. **In loving memory of my father.**

Zeval is a self-contained engine: no C library, no C runtime, no external dependencies. The Linux build is a static ELF that talks to the kernel through raw system calls; the Windows build is a single `zeval.exe` that imports nothing but `KERNEL32.dll`. Both are built from the same source. At a fixed depth they search the same tree and report identical node counts on Linux and Windows.

### Project Structure

The repository holds the engine and everything used to build, test and tune it:

- **Engine** (`src/`): the assembly source. Move generation, search, evaluation, UCI, the Windows platform layer and the thread code.
- **Build** (`build/`): `build.sh` (Linux, also cross-builds the Windows executable) and `build.bat` (Windows).
- **Tests** (`tests/`): Python test scripts. Fixed-depth regression, perft, UCI conformance, time management, Lazy SMP audits, a self-play match runner with SPRT.
- **Tactics** (`tactics/`): a tactical test suite of 1,146 positions with a runner, an A/B analysis and recorded reference results.
- **Tuner** (`tuner/`): Texel-style tuning of the evaluation in Python, with a bit-exact Python model of the engine's evaluation.
- **Tools** (`tools/`): magic bitboard and KPK bitbase generators, SPSA tuning of search parameters, candidate builds, script lint.

### Architecture Highlights

#### Search

Principal Variation Search with iterative deepening and aspiration windows.

- **Transposition table:** lock-free (key XOR data), two-entry buckets, aging, mate-score aware, also used in quiescence. The static evaluation is cached in the table entry. On Linux the table is backed by transparent huge pages.
- **Pruning and reductions:** null-move pruning, razoring, reverse futility pruning, futility pruning (on the LMR depth), late-move pruning, SEE pruning of captures and quiet moves, internal iterative reduction, late move reductions with history, cut-node and improving adjustments.
- **Extensions:** check extension and singular extension of the TT move (with multi-cut).
- **Move ordering:** TT move, captures by MVV-LVA plus capture history, split into good and bad by static exchange evaluation with a threshold that follows the capture history (bad captures after the quiet moves), killer moves, history with malus, continuation history (one and two plies back).
- **Quiescence:** captures and promotions, check evasions, SEE and delta pruning.
- **Pre-make pruning:** legality and "gives check" of a quiet move are decided from the bitboards before making it, so pruned moves are never made and unmade.

#### Parallelism

Lazy SMP with up to 32 threads.

- **Thread units:** the search code is assembled once per thread, and each copy has its own state block. No thread-local storage or OS thread API is involved in reaching per-thread state.
- **Thread pool:** helper threads are created once and wait between searches without using CPU time.
- **Shared state:** the threads share only the transposition table. Helpers use staggered aspiration windows.
- **Best move:** chosen by a vote over the root results of all threads.

#### Time Management

- **Allocation:** the time per move follows the game: a share of the remaining clock plus the expected increments, rising over the first moves of the game.
- **Adjustment per move:** the stop time is shortened when the best move is stable or one root move takes most of the nodes. It is lengthened when the best move changes or the score falls.
- **Safety:** a hard limit per move and a configurable Move Overhead.

#### Evaluation

A classical evaluation, tapered between middlegame and endgame:

- **Base:** material and piece-square tables (incremental), mobility over a pin-aware mobility area, bishop pair, tempo.
- **Pawn structure:** doubled, isolated, supported and phalanx pawns.
- **Passed pawns:** rank, king distances, file, free and defended path.
- **Pieces:** rooks on open and semi-open files, knight outposts.
- **King safety:** king shield, king danger (attackers, weak ring squares, safe checks, pins) and open lines against the king (open files, storming pawns, rooks and queens on the files without a shelter pawn).
- **Threats:** pawn threats, hanging pieces, restricted squares, threats against the queen.
- **Endgame scale factor:** drawish material, opposite-coloured bishops, wrong bishop, typical drawn single-pawn endings, rook endings, few pawns.
- **Special endgame knowledge:** mating patterns against a bare king (including KBNK), a KPK bitbase built into the binary, KQ-KR, KR-KB and KR-KN.

Every term is linear in its parameters. The values were tuned on about 4 million quiet positions; `tuner/check_eval.py` proves that the Python model reproduces the engine's evaluation exactly. The evaluation picks a POPCNT or a plain x86-64 code path at start-up.

### Building the Engine

#### Prerequisites

- **Linux, release build:** NASM 2.16 or newer, GNU `ld`, and LLVM (`lld-link`, `llvm-dlltool`, `llvm-rc`) for the Windows executable.
- **Windows:** NASM and the Visual Studio Build Tools (an "x64 Native Tools Command Prompt").
- **Optional, for tests, tactics and tuning:** Python 3.8 or newer with `python-chess`, plus `numpy` and `scipy` for the tuner.

#### Linux

```sh
sudo apt install nasm lld llvm
sh build/build.sh
```

This produces `dist/zeval` (Linux) and `dist/zeval.exe` (Windows, cross-built), each with 32 thread units.

A quick single-threaded build for experiments:

```sh
sh tests/build1.sh ./zeval
```

#### Windows

Open an "x64 Native Tools Command Prompt" with `nasm.exe` on the `PATH`:

```bat
build\build.bat
```

The executable is `dist\zeval.exe`.

#### Build Switches

Search and evaluation features are build-time switches, documented in the headers of `src/p1_data.asm` and `src/eval.asm`:

```sh
NASMFLAGS="-DSING=0" sh build/build.sh          # for example: without the singular extension
```

[docs/README.md](docs/README.md) maps every switch and tunable constant with its default value and its test status.

### Usage

Zeval speaks the Universal Chess Interface and runs in any UCI GUI or tournament manager (Arena, Cute Chess, fastchess, cutechess-cli, Lucas Chess, python-chess and others).

#### UCI Options

| Option | Default | Range | Description |
|---|---|---|---|
| `Hash` | 32 | 1 to 32768 | transposition table size in MB |
| `Clear Hash` | | button | empties the transposition table and the history tables |
| `Threads` | 1 | 1 to 32 | search threads (Lazy SMP) |
| `Move Overhead` | 12 | 0 to 5000 | time in ms reserved for GUI and network latency |

#### Supported UCI Commands

- `uci`, `isready`, `ucinewgame`, `setoption name <name> value <value>`
- `position [startpos | fen <fen>] moves <moves>`
- `go` with `depth`, `nodes`, `movetime`, `wtime` / `btime` / `winc` / `binc` / `movestogo`, `infinite`, `searchmoves`
- `stop`, `quit`

The output has the full principal variation, `seldepth`, `hashfull`, `currmove` lines during long searches, and `bestmove ... ponder ...`.

#### Console Mode

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

#### Requirements

An x86-64 CPU (SSE2; POPCNT and SSE4.1 are used when present) running Linux or 64-bit Windows 10 / 11.

### Testing

```sh
python3 tests/t_golden.py ./dist/zeval     # fixed-depth search == the recorded reference
python3 tests/t_perft.py                   # move generation against python-chess
python3 tests/t_uci.py                     # UCI conformance
./tactics.sh smoke                         # tactical smoke test (Windows: tactics.bat)
```

The test scripts use `zeval` (`zeval.exe` on Windows) in the root of the checkout, else the one in `dist/`; `ENGINE=path` selects another build.

On Windows, `verify.bat` checks a build against the recorded reference. `verify_smp.bat` and `verify_units.bat` also need test builds, made with `tools\build_testbuilds.bat` (Linux: `sh tools/build_testbuilds.sh`).

`tests/match.py` plays self-play matches with paired openings and a pentanomial SPRT; changes to the search and the evaluation are decided by such matches.

### Limitations

- **Not supported:** endgame tablebases, MultiPV, a `Ponder` option (`go ponder` is a normal timed search) and opening books.
- **Determinism:** a search with `Threads` above 1 is not deterministic; with one thread a fixed-depth search is.

### License

This project is licensed under the GNU General Public License v3.0. See the [LICENSE](LICENSE) file for details.

### Credits

See [CREDITS.md](CREDITS.md).

---

## Türkçe

Tamamı x86-64 assembly (NASM) ile yazılmış, klasik ve kademeli (tapered) bir değerlendirmeye sahip UCI satranç motoru. **Babamın aziz hatırasına.**

Zeval kendi kendine yeten bir motordur: C kütüphanesi, C runtime ya da harici bir bağımlılık yoktur. Linux sürümü, çekirdekle doğrudan sistem çağrılarıyla konuşan statik bir ELF'tir; Windows sürümü ise `KERNEL32.dll` dışında hiçbir şey içe aktarmayan tek bir `zeval.exe` dosyasıdır. İkisi de aynı kaynaktan derlenir. Sabit derinlikte aynı ağacı ararlar ve Linux ile Windows'ta birebir aynı node sayılarını verirler.

### Proje Yapısı

Depo, motoru ve onu derlemek, test etmek ve ayarlamak için kullanılan her şeyi içerir:

- **Motor** (`src/`): assembly kaynak kodu. Hamle üretimi, arama, değerlendirme, UCI, Windows platform katmanı ve thread kodu.
- **Derleme** (`build/`): `build.sh` (Linux; Windows çalıştırılabilir dosyasını da çapraz derler) ve `build.bat` (Windows).
- **Testler** (`tests/`): Python test betikleri. Sabit derinlik regresyonu, perft, UCI uyumluluğu, zaman yönetimi, Lazy SMP denetimleri, SPRT'li bir self-play maç yürütücüsü.
- **Taktikler** (`tactics/`): 1.146 pozisyonluk bir taktik test seti; çalıştırıcı, A/B analizi ve kayıtlı referans sonuçlarla birlikte.
- **Tuner** (`tuner/`): değerlendirmenin Python ile Texel tarzı ayarı; motorun değerlendirmesini bit düzeyinde birebir üreten bir Python modeliyle birlikte.
- **Araçlar** (`tools/`): magic bitboard ve KPK bitbase üreteçleri, arama parametrelerinin SPSA ile ayarı, aday derlemeler, betik denetimi (lint).

### Mimari

#### Arama

Iterative deepening ve aspiration window'larla Principal Variation Search.

- **Transposition table:** kilitsiz (anahtar XOR veri), iki girişli kovalar, yaşlandırma (aging), mat skorlarını doğru saklar, quiescence'ta da kullanılır. Statik değerlendirme tablo girişinde saklanır. Linux'ta tablo transparent huge page'lerle desteklenir.
- **Budama ve azaltmalar:** null-move budaması, razoring, reverse futility budaması, futility budaması (LMR derinliğinde), late-move budaması, taş alan ve sessiz hamlelerde SEE budaması, internal iterative reduction, history destekli late move reductions, cut-node ve improving ayarlamaları.
- **Uzatmalar:** şah uzatması ve TT hamlesinin singular uzatması (multi-cut ile).
- **Hamle sıralama:** TT hamlesi; MVV-LVA ve capture history'ye göre sıralı, eşiği capture history'ye göre kayan static exchange evaluation ile iyi ve kötü olarak ayrılan taş almalar (kötü almalar sessiz hamlelerden sonra), killer hamleler, malus'lu history, continuation history (bir ve iki yarım hamle geriye).
- **Quiescence:** taş almalar ve terfiler, şahtan kaçışlar, SEE ve delta budaması.
- **Hamle yapmadan budama:** sessiz bir hamlenin yasallığı ve şah çekip çekmediği, hamle yapılmadan bitboard'lardan belirlenir; böylece budanan hamleler hiç yapılıp geri alınmaz.

#### Paralellik

32 thread'e kadar Lazy SMP.

- **Thread birimleri:** arama kodu her thread için ayrı derlenir ve her kopyanın kendi durum bloğu vardır. Thread'e özel duruma ulaşmak için thread-local storage ya da işletim sisteminin thread API'si kullanılmaz.
- **Thread havuzu:** yardımcı thread'ler bir kez oluşturulur ve aramalar arasında CPU harcamadan bekler.
- **Paylaşılan durum:** thread'ler yalnızca transposition table'ı paylaşır. Yardımcı thread'ler kademeli aspiration window'lar kullanır.
- **En iyi hamle:** tüm thread'lerin kök sonuçları üzerinden oylamayla seçilir.

#### Zaman Yönetimi

- **Süre dağıtımı:** hamle başına süre oyunun gidişine göre belirlenir: kalan sürenin bir payı artı beklenen artışlar; bu pay oyunun ilk hamlelerinde giderek büyür.
- **Hamle başına ayarlama:** en iyi hamle sabit kaldığında ya da node'ların çoğu tek bir kök hamleye gittiğinde durma zamanı kısaltılır; en iyi hamle değiştiğinde ya da skor düştüğünde uzatılır.
- **Güvenlik:** hamle başına kesin bir üst sınır ve ayarlanabilir bir Move Overhead.

#### Değerlendirme

Orta oyun ile oyun sonu arasında kademeli geçiş yapan (tapered) klasik bir değerlendirme:

- **Temel:** materyal ve taş-kare tabloları (artımlı), açmazları hesaba katan bir hareket alanında mobilite, fil çifti, tempo.
- **Piyon yapısı:** çift, izole, desteklenen ve yan yana (phalanx) piyonlar.
- **Geçer piyonlar:** yatay sıra, şah mesafeleri, dikey, serbest ve korunan yol.
- **Taşlar:** açık ve yarı açık dikeylerde kaleler, at karakolları (outpost).
- **Şah güvenliği:** şah kalkanı, şah tehlikesi (saldıran taşlar, zayıf çevre kareleri, güvenli şah çekmeler, açmazlar) ve şaha karşı açık hatlar (açık hatlar, fırtına piyonları, siper piyonu olmayan hatlardaki kale ve vezirler).
- **Tehditler:** piyon tehditleri, korumasız taşlar, kısıtlanan kareler, vezire yönelik tehditler.
- **Oyun sonu ölçek faktörü:** beraberliğe yakın materyal, zıt renkli filler, yanlış fil, tipik berabere tek piyonlu oyun sonları, kale oyun sonları, az piyon.
- **Özel oyun sonu bilgisi:** yalnız şaha karşı mat kalıpları (KBNK dahil), binary'ye gömülü bir KPK bitbase'i, KQ-KR, KR-KB ve KR-KN.

Her terim kendi parametrelerine göre doğrusaldır. Değerler yaklaşık 4 milyon sessiz pozisyon üzerinde ayarlandı; `tuner/check_eval.py`, Python modelinin motorun değerlendirmesini birebir ürettiğini kanıtlar. Değerlendirme, açılışta POPCNT kullanan ya da düz x86-64 kod yolunu seçer.

### Derleme

#### Gereksinimler

- **Linux, release derlemesi:** NASM 2.16 ya da daha yenisi, GNU `ld` ve Windows çalıştırılabilir dosyası için LLVM (`lld-link`, `llvm-dlltool`, `llvm-rc`).
- **Windows:** NASM ve Visual Studio Build Tools ("x64 Native Tools Command Prompt").
- **İsteğe bağlı; testler, taktikler ve ayar için:** Python 3.8 ya da daha yenisi ile `python-chess`; tuner için ayrıca `numpy` ve `scipy`.

#### Linux

```sh
sudo apt install nasm lld llvm
sh build/build.sh
```

Bu komut `dist/zeval` (Linux) ve `dist/zeval.exe` (Windows, çapraz derlenmiş) dosyalarını üretir; her biri 32 thread birimi içerir.

Denemeler için hızlı, tek thread'li bir derleme:

```sh
sh tests/build1.sh ./zeval
```

#### Windows

`nasm.exe`'nin `PATH` üzerinde olduğu bir "x64 Native Tools Command Prompt" açın:

```bat
build\build.bat
```

Çalıştırılabilir dosya `dist\zeval.exe`'dir.

#### Derleme Anahtarları

Arama ve değerlendirme özellikleri derleme zamanında açılıp kapatılan anahtarlardır; `src/p1_data.asm` ve `src/eval.asm` dosyalarının başlıklarında belgelenmiştir:

```sh
NASMFLAGS="-DSING=0" sh build/build.sh          # örneğin: singular uzatma olmadan
```

Tüm anahtarların ve ayarlanabilir sabitlerin varsayılan değerleri ve test durumları [docs/README.md](docs/README.md) dosyasındadır.

### Kullanım

**Zeval**, Universal Chess Interface (UCI) protokolünü konuşur ve her UCI arayüzünde ya da turnuva yöneticisinde çalışır (Arena, Cute Chess, fastchess, cutechess-cli, Lucas Chess, python-chess ve diğerleri).

#### UCI Seçenekleri

| Seçenek | Varsayılan | Aralık | Açıklama |
|---|---|---|---|
| `Hash` | 32 | 1 - 32768 | transposition table boyutu (MB) |
| `Clear Hash` | | düğme | transposition table'ı ve history tablolarını boşaltır |
| `Threads` | 1 | 1 - 32 | arama thread'leri (Lazy SMP) |
| `Move Overhead` | 12 | 0 - 5000 | arayüz ve ağ gecikmesi için ayrılan süre (ms) |

#### Desteklenen UCI Komutları

- `uci`, `isready`, `ucinewgame`, `setoption name <name> value <value>`
- `position [startpos | fen <fen>] moves <moves>`
- `go`: `depth`, `nodes`, `movetime`, `wtime` / `btime` / `winc` / `binc` / `movestogo`, `infinite`, `searchmoves` ile
- `stop`, `quit`

Çıktıda tam ana varyant (principal variation), `seldepth`, `hashfull`, uzun aramalarda `currmove` satırları ve `bestmove ... ponder ...` yer alır.

#### Konsol Modu

Arayüz olmadan başlatıldığında Zeval terminalde oynar:

```sh
./zeval
```

Hamleleri `e2e4` biçiminde yazın (terfi: `e7e8q`). Diğer komutlar:

- `go`: motor hamle yapar.
- `new`: yeni oyun.
- `time <ms>`: hamle başına süre.
- `d`: tahtayı gösterir.
- `perft <n>`: hamle üretimi sayımı.
- `eval`: statik değerlendirme.
- `quit`: çıkış.

`uci` yazıldığında motor UCI moduna geçer.

#### Sistem Gereksinimleri

Linux ya da 64 bit Windows 10 / 11 çalıştıran bir x86-64 işlemci (SSE2; varsa POPCNT ve SSE4.1 kullanılır).

### Test

```sh
python3 tests/t_golden.py ./dist/zeval     # sabit derinlikte arama == kayıtlı referans
python3 tests/t_perft.py                   # hamle üretimi, python-chess ile karşılaştırma
python3 tests/t_uci.py                     # UCI uyumluluğu
./tactics.sh smoke                         # hızlı taktik testi (Windows: tactics.bat)
```

Test betikleri deponun kökündeki `zeval`'i (Windows'ta `zeval.exe`), yoksa `dist/` altındakini kullanır; başka bir derleme için `ENGINE=yol`.

Windows'ta `verify.bat`, bir derlemeyi kayıtlı referansla karşılaştırır. `verify_smp.bat` ve `verify_units.bat` ayrıca test derlemelerine ihtiyaç duyar; bunlar `tools\build_testbuilds.bat` ile üretilir (Linux: `sh tools/build_testbuilds.sh`).

`tests/match.py`, eşleştirilmiş açılışlar ve pentanomial SPRT ile self-play maçlar oynatır; arama ve değerlendirmedeki değişikliklere bu tür maçlarla karar verilir.

### Sınırlamalar

- **Desteklenmeyenler:** oyun sonu tablebase'leri, MultiPV, `Ponder` seçeneği (`go ponder` normal, süreli bir arama olarak işlenir) ve açılış kitapları.
- **Determinizm:** `Threads` 1'den büyükken arama deterministik değildir; tek thread ile sabit derinlikte arama deterministiktir.

### Lisans

Bu proje GNU General Public License v3.0 ile lisanslanmıştır. Ayrıntılar için [LICENSE](LICENSE)

### Teşekkürler

[CREDITS.md](CREDITS.md#türkçe)
