#!/usr/bin/env python3
"""Tactics benchmark: solve rate on an EPD suite (bm = best move(s), am = avoid move(s)).

usage: t_epd.py suite.epd ENGINE [ENGINE ...] [--movetime 100 | --nodes 200000] [--limit N] [--workers 2] [--json out.json]

  * One engine  : prints solved / total, mean depth, mean nps.
  * Two or more : also lists the positions solved by some engines but not by all (use this for A/B of a search or eval change).
  * --movetime  : equal time per position (an eval that makes the engine slower is charged for it).
  * --nodes     : equal node count per position (isolates the knowledge, ignores the speed).  Run both before judging a change.
A position counts as solved when the engine's final bestmove is one of bm, or (no bm given) is not one of am.
Needs python-chess.  Suites: Win At Chess (300), ECM, Bratko-Kopec ... any file with one EPD per line.
Note: WAC is old and easy (several positions have more than one winning move); use it as a smoke test and regression guard, not as an Elo estimate.
Informational: prints numbers.  Exit code 0; 1 = the file has no usable position; 2 = an engine could not be started.
Results are matched to positions by their index, not by the EPD id (two lines may share an id).  A position where the engine crashed or did not answer is counted
as unsolved, the engine is restarted for the next one, and the number of such positions is printed ("no answer N").
"""
import argparse, json, os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chess
from multiprocessing import Pool
from _eng import resolve, PipeReader


class Eng:
    def __init__(self, path):
        self.p = subprocess.Popen(resolve(path), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, bufsize=0)
        self.rd = PipeReader(self.p.stdout); self.buf = b""
        self.send("uci"); self.wait("uciok"); self.send("isready"); self.wait("readyok")

    def send(self, s):
        self.p.stdin.write((s + "\n").encode()); self.p.stdin.flush()

    def readline(self, timeout=0.5):
        end = time.time() + timeout
        while b"\n" not in self.buf:
            left = end - time.time()
            if left <= 0: return ""
            d = self.rd.get(left)
            if d is None: return ""
            if not d: raise RuntimeError("engine died")
            self.buf += d
        l, self.buf = self.buf.split(b"\n", 1)
        return l.decode()

    def wait(self, tok, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if self.readline().startswith(tok): return
        raise RuntimeError("timeout waiting for " + tok)

    def solve(self, fen, go, limit_s):
        self.send("ucinewgame"); self.send("isready"); self.wait("readyok")
        self.send("position fen " + fen); self.send(go)
        depth = nodes = nps = 0; end = time.time() + limit_s
        while time.time() < end:
            l = self.readline()
            if l.startswith("info") and " depth " in l and " score " in l:
                t = l.split()
                depth = int(t[t.index("depth") + 1])
                if "nodes" in t: nodes = int(t[t.index("nodes") + 1])
                if "nps" in t: nps = int(t[t.index("nps") + 1])
            elif l.startswith("bestmove"):
                return l.split()[1], depth, nodes, nps
        raise RuntimeError("no bestmove")

    def quit(self):
        try: self.send("quit"); self.p.wait(timeout=2)
        except Exception: self.p.kill()


def load_epd(path, limit):
    out = []
    for n, line in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
        line = line.strip()
        if not line or line.startswith("#"): continue
        b = chess.Board()
        try: ops = b.set_epd(line)
        except Exception as e:
            print(f"skip line {n}: {e}", file=sys.stderr); continue
        bm = [m.uci() for m in ops.get("bm", [])]
        am = [m.uci() for m in ops.get("am", [])]
        if not bm and not am:
            print(f"skip line {n}: no bm/am", file=sys.stderr); continue
        out.append({"id": str(ops.get("id", f"#{n}")), "fen": b.fen(), "bm": bm, "am": am, "idx": len(out)})
        if limit and len(out) >= limit: break
    return out


def run_chunk(args):
    path, go, limit_s, items = args
    e = Eng(path); res = []
    try:
        for it in items:
            try: mv, d, nodes, nps = e.solve(it["fen"], go, limit_s)
            except (RuntimeError, OSError):                   # engine died / no answer: unsolved, start a fresh engine for the next position
                mv, d, nodes, nps = "", 0, 0, 0
                e.quit(); e = Eng(path)
            ok = (mv in it["bm"]) if it["bm"] else (mv != "" and mv not in it["am"])
            res.append({"idx": it["idx"], "id": it["id"], "move": mv, "ok": ok, "depth": d, "nodes": nodes, "nps": nps})
    finally:
        e.quit()
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("epd"); ap.add_argument("engines", nargs="+")
    ap.add_argument("--movetime", type=int, default=100)
    ap.add_argument("--nodes", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    suite = load_epd(a.epd, a.limit)
    if not suite: sys.exit("no usable positions")
    go = f"go nodes {a.nodes}" if a.nodes else f"go movetime {a.movetime}"
    limit_s = 60 if a.nodes else a.movetime / 1000 + 5
    w = max(1, a.workers)
    chunks = [suite[i::w] for i in range(w)]
    allres = {}
    for path in a.engines:                                   # pre-flight: every engine must start and answer uci / isready (clean message instead of a pool traceback)
        try: Eng(path).quit()
        except (RuntimeError, OSError) as ex: sys.stderr.write(f"ERROR: cannot run the engine {path!r}: {ex}\n"); sys.exit(2)
    for path in a.engines:
        t0 = time.time()
        with Pool(w) as pool:
            parts = pool.map(run_chunk, [(path, go, limit_s, c) for c in chunks])
        byidx = {r["idx"]: r for p in parts for r in p}
        rows = [byidx[it["idx"]] for it in suite]
        allres[path] = rows
        solved = sum(r["ok"] for r in rows)
        md = sum(r["depth"] for r in rows) / len(rows)
        nps = [r["nps"] for r in rows if r["nps"]]
        print(f"{os.path.basename(path):14s} {go:20s} solved {solved:3d}/{len(rows)} ({100*solved/len(rows):5.1f}%)  "
              f"mean depth {md:4.1f}  mean nps {sum(nps)/max(1,len(nps))/1e6:5.2f}M  [{time.time()-t0:.0f}s]"
              + (f"  no answer {sum(1 for r in rows if not r['move'])}" if any(not r['move'] for r in rows) else ""), flush=True)
    if len(a.engines) > 1:
        names = [os.path.basename(p) for p in a.engines]
        diff = [i for i in range(len(suite)) if len({allres[p][i]["ok"] for p in a.engines}) > 1]
        print(f"\npositions where engines differ: {len(diff)}")
        for i in diff:
            cells = "  ".join(f"{n}:{'OK ' if allres[p][i]['ok'] else 'no '}{allres[p][i]['move']:6s}"
                              for n, p in zip(names, a.engines))
            print(f"  {suite[i]['id']:10s} bm={','.join(suite[i]['bm']) or '-'} am={','.join(suite[i]['am']) or '-'}   {cells}")
    if a.json:
        json.dump({"suite": suite, "go": go, "results": allres}, open(a.json, "w"), indent=1)
