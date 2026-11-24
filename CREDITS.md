# Credits

[English](#english) · [Türkçe](#türkçe)

---

## English

**Zeval** exists because of people who, for decades, shared what they learned about teaching machines to play chess. This file is a thank-you to them.

### In memory of my father

This engine is written in loving memory of my father, who taught me chess.

### The chess programming community

To everyone who has given their heart to chess programming: the researchers, engine authors, forum posters, wiki editors, testers and tuners who have produced algorithms, published papers, answered questions and opened their source code for more than half a century. Alpha-beta, transposition tables, null-move pruning, late move reductions, static exchange evaluation, magic bitboards, Texel-style tuning, Lazy SMP: every one of these exists because somebody explained it in public. This engine is a small piece of that shared work.

### The Chess Programming Wiki and Lichess

- **[Chess Programming Wiki](https://www.chessprogramming.org/)**, founded by Mark Lefler in 2007 and kept alive by Gerd Isenberg and the CPW team. It was my main reference while writing this engine.
- **[Lichess](https://lichess.org/)**, a free and open-source chess server, now hosts the Chess Programming Wiki. Lichess also helped me a great deal during development. Please consider [supporting Lichess](https://lichess.org/patron).

### TalkChess

**[TalkChess](https://talkchess.com/)**, the Computer Chess Club forum, and everyone who answers questions there, from a first perft bug to the finest details of search and evaluation. I learned a great deal from its discussions. Thank you to its members and moderators.

### Open-source engines and the people behind them

This engine was shaped by what I learned from many open-source engines. Here I name those whose contribution I can point to specifically:

- **Stockfish** and its developers: ideas for time management and Lazy SMP thread selection, and the reference for endgame knowledge and tactical comparisons.
- **Ethereal** (Andrew Grant) and **Stash** (Gediminas Masaitis): positions from their games were used as labelled data while tuning the evaluation.
- **Andrew Grant**'s paper *Evaluation & Tuning in Chess Engines* (2020): the method behind `tuner/resolve.py`, which builds training positions from the engine's own games by playing out the principal variation of a fixed-depth search.
- **Zurichess** (Alexandru Mosoi): the quiet-labelled position set used as tuning data.
- **Arasan** (Jon Dart): test positions.

<!-- Add here any other engine whose source you studied, with its author. -->

### Data, tables and tools

- **PeSTO piece-square tables** (Ronald Friederich, Rofchade): the starting values of the tables, later retuned.
- **Texel tuning** (Peter Österlund): the method behind the evaluation tuner.
- **Syzygy tablebases** (Ronald de Man): used to calibrate and fit endgame evaluation.
- **noob_3moves opening book**, from the book collection of the Stockfish project ([official-stockfish/books](https://github.com/official-stockfish/books), CC0 1.0): the start positions of the test matches and of the self-play data (`tests/books/noob_3moves.epd`, unchanged).
- **python-chess** (Niklas Fiekas): used throughout the test scripts.
- **cutechess**, **fastchess**, **Arena**, **Lucas Chess**: the GUIs and tournament managers the engine was run with or is meant to run in.
- **Classic test suites** (Win At Chess by Fred Reinfeld, ECM, Arasan, and others): used locally to check tactics.

### Claude

This engine was developed with the assistance of **[Claude](https://www.anthropic.com/claude)**, the AI assistant made by Anthropic, and guided throughout by human knowledge and decisions: what to build, what to test, what to keep and what to throw away.

### About the code

The ideas in this engine come from the sources above and from the shared knowledge of the field. If you believe something here is missing or wrongly credited, please open an issue.

---

## Türkçe

**Zeval**, makinelere satranç oynamayı öğretmek için öğrendiklerini yıllardır başkalarıyla paylaşan insanların emeği sayesinde var. Bu dosya, onlara bir teşekkürdür.

### Babamın anısına

Bu motor, bana satrancı öğreten babamın aziz hatırasına adanmıştır.

### Satranç programlama topluluğu

Gönlünü satranç programlamaya vermiş herkese: yarım asırdan uzun süredir algoritma geliştiren, makale yazan, soru yanıtlayan ve kodunu herkese açan araştırmacılara, motor yazarlarına, forum üyelerine, wiki editörlerine, test edenlere ve parametre ayarlayanlara teşekkür ederim. Alfa-beta, transposition table, null-move budaması, late move reductions, static exchange evaluation, magic bitboard, Texel tarzı parametre ayarı, Lazy SMP... Bunların her biri, birileri bildiğini herkesle paylaştığı için bugün hepimizin elinde. Bu motor, o ortak emeğin küçük bir parçasıdır.

### Chess Programming Wiki ve Lichess

- **[Chess Programming Wiki](https://www.chessprogramming.org/)**, 2007'de Mark Lefler tarafından kuruldu ve Gerd Isenberg ile CPW ekibi sayesinde yaşamaya devam ediyor. Bu motoru yazarken başvurduğum başlıca kaynak oldu.
- Ücretsiz ve açık kaynaklı satranç sunucusu **[Lichess](https://lichess.org/)**, artık Chess Programming Wiki'ye ev sahipliği yapıyor. Geliştirme sürecinde bana da çok yardımı dokundu. [Lichess'e destek olmayı](https://lichess.org/patron) düşünebilirsiniz.

### TalkChess

**[TalkChess](https://talkchess.com/)** (Computer Chess Club forumu) ve orada soru yanıtlayan, deneyimini paylaşan herkes: ilk perft hatasından arama ve eval'in en ince ayrıntılarına kadar. Oradaki tartışmalardan çok şey öğrendim. Üyelerine ve moderatörlerine teşekkürler.

### Açık kaynak motorlar ve geliştiricileri

Bu motor, pek çok açık kaynak motordan öğrendiklerimle şekillendi. Burada, katkısını somut olarak gösterebildiklerimi anıyorum:

- **Stockfish** ve geliştiricileri: zaman yönetimi ile Lazy SMP'de thread seçimi için fikir kaynağı; oyun sonu bilgisi ve taktik karşılaştırmaları için başvuru noktası.
- **Ethereal** (Andrew Grant) ve **Stash** (Gediminas Masaitis): oyunlarından alınan pozisyonlar, eval'i ayarlarken etiketli veri olarak kullanıldı.
- **Andrew Grant**'ın *Evaluation & Tuning in Chess Engines* (2020) makalesi: `tuner/resolve.py`'nin yöntemi; eğitim pozisyonları, motorun kendi oyunlarından sabit derinlikli bir aramanın ana varyantı oynanarak elde ediliyor.
- **Zurichess** (Alexandru Mosoi): parametre ayarında kullanılan, "sessiz" (quiet) olarak etiketlenmiş pozisyon kümesi.
- **Arasan** (Jon Dart): test pozisyonları.

<!-- Kaynak kodunu incelediğiniz diğer motorları, yazarlarıyla birlikte buraya ekleyin. -->

### Veri, tablolar ve araçlar

- **PeSTO taş-kare tabloları** (Ronald Friederich, Rofchade): tabloların başlangıç değerleri olarak kullanıldı, sonrasında yeniden ayarlandı.
- **Texel yöntemi** (Peter Österlund): eval tuner'ının dayandığı parametre ayarı yöntemi.
- **Syzygy tablebase'leri** (Ronald de Man): oyun sonu değerlendirmesini kalibre etmek ve ayarlamak için kullanıldı.
- **noob_3moves açılış kitabı**, Stockfish projesinin kitap koleksiyonundan ([official-stockfish/books](https://github.com/official-stockfish/books), CC0 1.0): test maçlarının ve self-play verisinin başlangıç pozisyonları (`tests/books/noob_3moves.epd`, değiştirilmeden).
- **python-chess** (Niklas Fiekas): tüm test betiklerinde kullanıldı.
- **cutechess**, **fastchess**, **Arena**, **Lucas Chess**: motoru çalıştırdığım ya da çalışmasını hedeflediğim arayüzler ve turnuva yöneticileri.
- **Klasik test setleri** (Fred Reinfeld'in Win At Chess'i, ECM, Arasan ve diğerleri): taktikleri kontrol etmek için yalnızca yerelde kullanıldı, bu repoda dağıtılmıyor.

### Claude

Bu motor, Anthropic'in geliştirdiği yapay zekâ asistanı **[Claude](https://www.anthropic.com/claude)**'un yardımıyla geliştirildi. Süreç boyunca motor tasarımı insan bilgisi ve kararlarıyla yönlendirildi: neyin yapılacağına, neyin test edileceğine, neyin tutulup neyin atılacağına insan karar verdi.

### Kod hakkında

Bu motordaki fikirler yukarıda anılan kaynaklardan ve alanın ortak birikiminden geliyor. Eksik ya da yanlış verilmiş bir atıf olduğunu düşünüyorsanız lütfen bir issue açın.
