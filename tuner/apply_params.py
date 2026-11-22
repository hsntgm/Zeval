#!/usr/bin/env python3
"""Write tuned parameters (tune.py's params.json) into the engine source src/eval.asm:  the pst_mg / pst_eg tables, the evp table (terms of
the full evaluation) and the BP_MG / BP_EG / TEMPO defaults.  The material tables (matmg / mateg) are NOT touched: tune.py optimises
"pst + material" and returns pst = merged - material.

usage:  python3 tuner/apply_params.py params.json                  patch src/eval.asm IN PLACE (the original is saved once as tuner/eval.asm.orig)
        python3 tuner/apply_params.py params.json --out-dir X      write X/eval.asm instead, src/ stays untouched
        python3 tuner/apply_params.py --revert                     put the saved original back
        --force                                                    apply although the safety gate refuses (see below)
SAFETY GATE: params.json is refused (nothing is written) unless
  * it carries the asm baseline written by tune.py (loss.asm_val; a params.json of an older tuner has none),
  * the validation loss of its ROUNDED tables is not worse than the loss of the asm tables as given (so a collapsed / all-zero / over-fitted
    result - e.g. --from-zero on too little data - cannot be applied by accident), and
  * the mean |eval| did not collapse or explode (tuned / asm within 0.5 .. 2.0).
The gate only protects against broken output; whether a tuned eval is BETTER is decided by the SPRT alone.
Afterwards: rebuild (build/build.bat on Windows, sh build/build.sh on Linux) and run  python3 tuner/check_eval.py --engine <new exe>
(the written tables are read back from the file and compared with the intended values first)."""
import argparse, json, os, re, shutil, sys, time
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model


def rows(vals):
    return "\n".join("    dw " + ", ".join(f"{int(x):4d}" for x in vals[i:i + 8]) for i in range(0, 384, 8))


def evp_rows(mg, eg):
    out, prev = [], None
    for j in range(model.EV_N):
        name = model.param_name(model.EVP0 + j).split("[")[0]
        if name != prev:
            out.append(f"; {name}"); prev = name
        out.append(f"    SC {int(mg[j]):4d}, {int(eg[j]):4d}")
    return "\n".join(out) + "\n"


def patch(src, tab, note):
    a = src.index("pst_mg:\n"); c = src.index("\nevp:") + 1
    d = src.index("\n", c) + 1; e = src.index("evp_end:")
    new = (src[:a] + "pst_mg:\n" + rows(tab["pst_mg"]) + "\n" + "pst_eg:\n" + rows(tab["pst_eg"]) + "\n\n" + src[c:d]
           + evp_rows(tab["evp_mg"], tab["evp_eg"]) + src[e:])
    for name, key in (("BP_MG", "bp_mg"), ("BP_EG", "bp_eg"), ("TEMPO", "tempo")):
        new, n = re.subn(r"(?m)^(%ifndef " + name + r"\n%define " + name + r"\s+)-?\d+", lambda m: m.group(1) + str(tab[key]), new)
        if n != 1:
            raise SystemExit(f"{name} default not found in the source (is this an eval.asm with the expected layout?)")
    anchor = "; piece-square tables: rank 8 first"
    if anchor not in new:
        raise SystemExit("the comment line in front of pst_mg was not found (is this an eval.asm with the expected layout?)")
    new = new.replace(anchor, note + "\n" + anchor, 1) if note not in new else new
    return new


def gate(P, tab):
    """list of reasons why params P must not be applied (empty = fine)"""
    why = []
    L = P.get("loss", {})
    if "asm_val" not in L or "rounded_val" not in L:
        why.append("no baseline in this params.json (written by an older tune.py): cannot tell whether it beats the asm tables - re-run tune.py")
    elif L["rounded_val"] > L["asm_val"] + 1e-9:
        why.append(f"validation loss of the rounded tables {L['rounded_val']:.6f} is WORSE than that of the asm tables {L['asm_val']:.6f}")
    m = P.get("mean_abs_eval")
    if m and m.get("asm", 0) > 0:
        ratio = m["tuned"] / m["asm"]
        if not 0.5 <= ratio <= 2.0:
            why.append(f"mean |eval| changed by a factor {ratio:.2f} (allowed 0.5 .. 2.0): the eval collapsed or exploded")
    return why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("params", nargs="?"); ap.add_argument("--src", default=model.ASM)
    ap.add_argument("--out-dir", default=""); ap.add_argument("--revert", action="store_true")
    ap.add_argument("--force", action="store_true", help="apply although the safety gate refuses")
    a = ap.parse_args()
    backup = os.path.join(HERE, "eval.asm.orig")
    if a.revert:
        if not os.path.exists(backup): raise SystemExit("no saved original")
        shutil.copyfile(backup, a.src); print("restored", a.src); return
    if not a.params: ap.error("params.json needed")
    try:
        P = json.load(open(a.params))
        P["tables"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SystemExit(f"{a.params} is not a params.json written by tune.py ({type(e).__name__}: {e})")
    tab = {k: (np.array(v, np.int64) if isinstance(v, list) else int(v)) for k, v in P["tables"].items()}
    if "evp_mg" not in tab:
        raise SystemExit(f"{a.params} has no evp tables: it was written by a tuner for an older evaluation - re-extract the data and re-tune")
    assert tab["pst_mg"].size == 384 and tab["pst_eg"].size == 384 and tab["evp_mg"].size == model.EV_N and tab["evp_eg"].size == model.EV_N
    why = gate(P, tab)
    if why:
        print("apply_params: SAFETY GATE refuses " + a.params + ":")
        for w in why: print("   - " + w)
        if not a.force:
            raise SystemExit("nothing written (use --force to override)")
        print("   --force given: applying anyway")
    ci = P.get("loss", {}).get("delta_val_ci")
    if ci and ci[2] >= 0:
        print(f"apply_params: note: the validation gain over the asm tables is not statistically significant (95 % interval [{ci[1]:+.6f}, {ci[2]:+.6f}]); only an SPRT can say whether this is better")
    m = P.get("mean_abs_eval")
    if m and m.get("asm", 0) > 0 and abs(m["tuned"] / m["asm"] - 1.0) > 0.02:
        print(f"apply_params: note: mean |eval| of the tuned tables is {m['tuned'] / m['asm']:.3f} x that of the asm tables.  The search margins (RFP, futility, razor, NMP, delta; SPSA T0..T19, T58) "
              f"are tuned for the old scale: such a shift alone costs Elo (measured once: about -30 Elo for +-10 %) and hides the gain of the tuning in an SPRT.  "
              f"Run tune.py with --norm-scale asm (or re-tune the margins with SPSA) before measuring.")
    src = open(a.src).read()
    note = (f"; (tuned by tuner/tune.py: {time.strftime('%Y-%m-%d')}, {P['positions_train'] + P['positions_val']} positions, K {P['K']:.3f}, prior {P['prior']:g}, free '{P['free']}';"
            f" the tables below are pst = tuned(pst + material) - material, BP {tab['bp_mg']}/{tab['bp_eg']}, tempo {tab['tempo']})")
    new = patch(src, tab, note)
    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True); dst = os.path.join(a.out_dir, "eval.asm")
    else:
        dst = a.src
        if not os.path.exists(backup): shutil.copyfile(a.src, backup)
    open(dst, "w").write(new)
    # read back and compare
    t = model.load_tables(dst)
    ok = all(np.array_equal(t[k], tab[k]) for k in ("pst_mg", "pst_eg", "evp_mg", "evp_eg")) and all(t[k] == tab[k] for k in ("bp_mg", "bp_eg", "tempo"))
    if not ok: raise SystemExit("read-back mismatch - file NOT trustworthy")
    print(f"apply_params: {dst} patched (read-back check OK): BP {t['bp_mg']}/{t['bp_eg']}, tempo {t['tempo']}")
    if not a.out_dir:
        print("next: rebuild the engine, then  python3 tuner/check_eval.py --engine <new exe>   (and tests/t_eval.py, tests/t_golden.py --make ...)")


if __name__ == "__main__":
    main()
