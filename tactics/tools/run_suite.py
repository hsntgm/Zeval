#!/usr/bin/env python3
"""Tactical suite runner (UCI, one position at a time, sequential, Threads=1).
usage: run_suite.py NAME ENGINE_PATH --movetime MS [--suites a,b,c] [--hash 64] [--limit N] [--out FILE.jsonl]
  (suites are read from tactics/suites/ and results go to tactics/results/NAME_<ms>ms.jsonl by default, whatever the current directory is;
   ENGINE_PATH may also be a command line such as "wine zeval.exe";  --limit N = only the first N positions of every suite)
Per position: ucinewgame (clears TT) -> position fen -> go movetime.  Solved = FINAL bestmove in bm (or not in am).
Also records time-to-solution (TTS): the earliest time after which every later PV head (and the final move) is correct.
Resumable: positions already in --out are skipped."""
import argparse, json, os, queue, shlex, subprocess, sys, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # .../tactics

def _cmd(s):
    return [s] if os.path.exists(s) else shlex.split(s, posix=(os.name != "nt"))

class Eng:
    def __init__(self, path, hash_mb):
        self.p = subprocess.Popen(_cmd(path), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.q = queue.Queue(); threading.Thread(target=self._rd, daemon=True).start()
        self.send("uci"); self.wait("uciok"); self.send(f"setoption name Hash value {hash_mb}"); self.send("setoption name Threads value 1")
        self.send("isready"); self.wait("readyok")
    def _rd(self):
        buf = b""
        while True:
            d = self.p.stdout.read(4096)
            if not d: self.q.put(None); return
            buf += d
            while b"\n" in buf:
                l, buf = buf.split(b"\n", 1); self.q.put(l.decode(errors="replace").strip())
    def send(self, s): self.p.stdin.write((s+"\n").encode()); self.p.stdin.flush()
    def get(self, to):
        try: return self.q.get(timeout=to)
        except queue.Empty: return ""
    def wait(self, tok, to=15):
        end = time.time()+to
        while time.time() < end:
            l = self.get(0.5)
            if l is None: raise RuntimeError("engine died")
            if l.startswith(tok): return
        raise RuntimeError("timeout waiting for "+tok)
    def close(self):
        try: self.send("quit"); self.p.wait(timeout=3)
        except Exception: self.p.kill()

def run_pos(e, row, movetime):
    e.send("ucinewgame"); e.send("isready"); e.wait("readyok")
    e.send("position fen "+row["fen"]); t0 = time.time(); e.send(f"go movetime {movetime}")
    hist = []; depth = nodes = nps = 0; score = None; best = None; stopped = False
    while True:
        l = e.get(0.25)
        if l is None: raise RuntimeError("engine died")
        el = (time.time()-t0)*1000
        if not l:
            if el > movetime+4000 and not stopped: e.send("stop"); stopped = True
            if el > movetime+9000: best = "TIMEOUT"; break
            continue
        if l.startswith("info") and " pv " in l and " depth " in l:
            t = l.split(); depth = int(t[t.index("depth")+1])
            if "nodes" in t: nodes = int(t[t.index("nodes")+1])
            if "nps" in t: nps = int(t[t.index("nps")+1])
            if "score" in t:
                k = t.index("score"); score = (t[k+1], int(t[k+2]))
            ms = int(t[t.index("time")+1]) if "time" in t else int(el)
            hist.append((ms, t[t.index("pv")+1]))
        elif l.startswith("bestmove"): best = l.split()[1]; break
    bm, am = row["bm"], row["am"]
    ok = lambda m: (m in bm) if bm else (m not in am)
    if bm and am: ok = lambda m: (m in bm) and (m not in am)
    solved = best not in (None,"TIMEOUT","(none)") and ok(best)
    tts = None
    if solved:
        tts = 0
        for ms, m in reversed(hist):     # walk back until the PV head was wrong
            if ok(m): tts = ms
            else: break
        if not hist: tts = int((time.time()-t0)*1000)
    return dict(suite=row["suite"], id=row["id"], fen=row["fen"], bm=bm, am=am, best=best, solved=solved, tts=tts,
                depth=depth, nodes=nodes, nps=nps, score=score, wall=int((time.time()-t0)*1000))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("name"); ap.add_argument("engine")
    ap.add_argument("--movetime", type=int, default=1000); ap.add_argument("--suites", default="arasan2026,iq4,ecmgcp,sbd,wac,classics,mates")
    ap.add_argument("--hash", type=int, default=64); ap.add_argument("--out"); ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    out = a.out or os.path.join(ROOT, "results", f"{a.name}_{a.movetime}ms.jsonl")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    done = set()
    if os.path.exists(out):
        for l in open(out):
            try: r = json.loads(l); done.add((r["suite"], r["id"], r["fen"]))
            except Exception: pass
    rows = []
    for s in a.suites.split(","):
        fn = os.path.join(ROOT, "suites", f"{s}.jsonl")
        if not os.path.exists(fn): sys.exit(f"unknown suite '{s}' (have: " + ", ".join(sorted(x[:-6] for x in os.listdir(os.path.join(ROOT, 'suites')) if x.endswith('.jsonl'))) + ")")
        rs = [json.loads(l) for l in open(fn)]
        rows += rs[:a.limit] if a.limit > 0 else rs
    todo = [r for r in rows if (r["suite"], r["id"], r["fen"]) not in done]
    print(f"{a.name}: {len(todo)} positions to run ({len(done)} already done), {a.movetime} ms each", flush=True)
    e = Eng(a.engine, a.hash); n = 0; t_start = time.time()
    with open(out, "a") as f:
        for r in todo:
            try: res = run_pos(e, r, a.movetime)
            except RuntimeError as ex:
                print("engine problem:", ex, "- restarting", flush=True); e.close(); e = Eng(a.engine, a.hash)
                res = dict(suite=r["suite"], id=r["id"], fen=r["fen"], bm=r["bm"], am=r["am"], best="ERROR", solved=False, tts=None, depth=0, nodes=0, nps=0, score=None, wall=0)
            f.write(json.dumps(res)+"\n"); f.flush(); n += 1
            if n % 100 == 0: print(f"  {n}/{len(todo)}  {time.time()-t_start:.0f}s", flush=True)
    e.close(); print("finished", a.name, f"{time.time()-t_start:.0f}s", "->", out, flush=True)
    summ = {}
    for l in open(out):
        try: r = json.loads(l)
        except Exception: continue
        if r["suite"] in a.suites.split(","): s = summ.setdefault(r["suite"], [0, 0]); s[0] += bool(r["solved"]); s[1] += 1
    for s, (ok, n) in summ.items(): print(f"   {a.name}  {s:11s} {ok:4d} / {n:4d} = {100*ok/n:5.1f} %", flush=True)
