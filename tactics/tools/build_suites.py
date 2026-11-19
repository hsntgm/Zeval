#!/usr/bin/env python3
"""Normalise the downloaded EPD suites into clean JSONL: {suite,id,fen,bm:[uci],am:[uci]}.
Handles: NUL / CR bytes, 'bm' after a stray ';', SAN or LAN (uci) moves, 'Qd1 +' style check marks, multiple bm, 'am'.
A line is DROPPED (and counted) if the position is illegal or a bm/am move is not legal in it."""
import re, json, sys, os
import chess
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # .../tactics
E = os.path.join(ROOT, "suites_epd")                                      # the EPD files as downloaded (flat folder)
OUT = os.path.join(ROOT, "suites")
SUITES = {  # name: (path, description)
 "arasan2026": (f"{E}/arasan2026.epd", "Arasan Test Suite 2026 (J. Dart) - hard tactical + endgame, newest"),
 "iq4":        (f"{E}/iq4.epd",        "IQ4 - hard tactical middlegame positions"),
 "ecmgcp":     (f"{E}/ecmgcp.epd",     "ECM / GCP - Encyclopedia of Chess Middlegames, tactics"),
 "sbd":        (f"{E}/silent-but-deadly.epd",  "Silent But Deadly - quiet-move tactics (Chris Whittington collection)"),
 "wac":        (f"{E}/wacnew.epd",     "Win At Chess (300) - classic, saturated; identical to tests/wac.epd of the engine package"),
 "classics":   (None, "LCT II/Lapuce2 + Kaufman + Bratko-Kopec + Nolot + BT2630 (small classic sets, concatenated)"),
 "mates":      (f"{E}/hard-mates-blass-uri.epd", "Hard mates (Blass/Uri), distance-to-mate 13-26 moves"),
}
CLASSIC_PARTS = [("lapuce2",f"{E}/lapuce2.epd"),("kaufman",f"{E}/kaufman.epd"),
                 ("bk",f"{E}/bratko-kopec.epd"),("nolot",f"{E}/nolot.epd"),("bt2630",f"{E}/bt2630.epd")]

def parse_line(raw):
    s = raw.replace("\x00","").replace("\r","").strip()
    s = re.sub(r"( [-KQkq]+) -(\d+) (\d+)", r"\1 - \2 \3", s)   # '- -1 12' typo -> '- - 1 12' (ep field glued to halfmove clock)
    if not s or s.lower().startswith("startpos"): return None
    t = s.split()
    if len(t) < 4: return None
    fen4 = " ".join(t[:4]); rest = " ".join(t[4:])
    # optional halfmove/fullmove numbers (FEN style) directly after the 4 fields
    m = re.match(r"^(\d+)\s+(\d+)\s*(.*)$", rest)
    if m: rest = m.group(3)
    ops = {}
    for part in rest.split(";"):
        part = part.strip()
        if not part: continue
        mm = re.match(r"^(bm|am|id|dm|c0|c1)\b\s*(.*)$", part)
        if mm: ops[mm.group(1)] = mm.group(2).strip()
        elif not ops and part.startswith(("bm ","am ")): pass
    # opcodes written without ';' (hardtestpos style  '... bm g6g4')
    if not ops:
        mm = re.match(r"^(bm|am)\s+(.*)$", rest)
        if mm: ops[mm.group(1)] = mm.group(2)
    if not ops:                                    # '... 1 1 Be2 dm 21'  (move without opcode, mates file)
        mm = re.match(r"^(\S+)\s+dm\s+\d+", rest)
        if mm: ops["bm"] = mm.group(1)
    # 'bm Rc8+ dm 26' (no ';'): cut at ' dm '
    for k in ("bm","am"):
        if k in ops: ops[k] = re.split(r"\s+dm\s+", ops[k])[0]
    return fen4, ops

def moves(board, txt):
    out = []
    for tok in re.split(r"[,\s]+", txt.replace('"','')):
        tok = tok.strip()
        if not tok or tok in "+#": continue
        tok = tok.rstrip("+#!?")
        mv = None
        try: mv = board.parse_san(tok)
        except Exception:
            try: mv = board.parse_uci(tok.lower())
            except Exception: mv = None
        if mv is None or mv not in board.legal_moves: return None
        out.append(mv.uci())
    return out

def load(name, path, prefix=None):
    rows, dropped = [], []
    with open(path, "rb") as f: lines = f.read().decode("latin-1").split("\n")
    for i, raw in enumerate(lines, 1):
        p = parse_line(raw)
        if p is None: continue
        fen4, ops = p
        if "bm" not in ops and "am" not in ops: dropped.append((i,"no bm/am")); continue
        try: b = chess.Board(fen4 + " 0 1")
        except Exception as e: dropped.append((i,"bad fen")); continue
        if not b.is_valid(): dropped.append((i,"illegal position")); continue
        bm = moves(b, ops["bm"]) if "bm" in ops else []
        am = moves(b, ops["am"]) if "am" in ops else []
        if bm is None or am is None or not (bm or am): dropped.append((i,"unparsable/illegal move")); continue
        pid = ops.get("id","").strip('" ') or f"line{i}"
        rows.append(dict(suite=name, id=(prefix+":" if prefix else "")+pid, fen=b.fen(), bm=bm, am=am, dm=("dm" in ops) or None))
    return rows, dropped

if __name__ == "__main__":
    summary = {}
    for name,(path,desc) in SUITES.items():
        if name == "classics":
            rows, dropped = [], []
            for pre,p in CLASSIC_PARTS:
                r,d = load(name,p,pre); rows += r; dropped += [(pre,)+x for x in d]
        else:
            rows, dropped = load(name, path)
        seen = set(); uniq = []
        for r in rows:
            k = (r["fen"], tuple(r["bm"]), tuple(r["am"]))
            if k in seen: continue
            seen.add(k); uniq.append(r)
        with open(os.path.join(OUT, f"{name}.jsonl"),"w") as f:
            for r in uniq: f.write(json.dumps(r)+"\n")
        summary[name] = dict(positions=len(uniq), dupes_removed=len(rows)-len(uniq), dropped=dropped, desc=desc,
                             with_am=sum(1 for r in uniq if r["am"]), multi_bm=sum(1 for r in uniq if len(r["bm"])>1))
        print(f"{name:11s} kept {len(uniq):4d}  dup {len(rows)-len(uniq):2d}  dropped {len(dropped):2d} {dropped[:4]}  am={summary[name]['with_am']} multi-bm={summary[name]['multi_bm']}")
    json.dump(summary, open(os.path.join(OUT, "_summary.json"),"w"), indent=1)
