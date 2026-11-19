#!/usr/bin/env python3
"""Lazy SMP - audit of the HELPER code: a helper thread must not write ANY shared variable except the contents of the
transposition table (and the stack / its own state block).  A helper that did would be a silent data race.

Linux only (needs nasm, nm, objdump).  Assembles the release flavour (SMP = 1, no -DUNITTEST, no -DWIN) of unit 1 (a helper) and unit 0 (the main thread),
and reads the machine code of the helper object:

  1. every instruction of unit 1 that carries a relocation (= names a symbol or a section offset) is classified:
       - memory operand that is WRITTEN (mov / add / inc / bts / xchg / lock ... with the memory operand first) or READ (cmp / test / bt / mov reg, [mem] ...);
       - an ADDRESS taken (mov reg, imm32 / lea) of a symbol.
  2. a WRITE must hit the unit's own state block (tlb_1 .. tlb_end), or the stack-less local data of the unit (nothing else exists); a write to an
     undefined symbol (= a variable of unit 0) or to any other place FAILS.
  3. an address taken of an undefined symbol (a variable or table of unit 0) must be a READ-ONLY table on the list below, because everything that is
     written through such an address would be invisible to step 2.  (The transposition table is reached through the pointer in the variable ttbase,
     not through a symbol address; with CH_SHARED the main thread's continuation-history table is reached the same way through chbase.
     Those two are the only intended shared writes; tests/t_chshared.py checks that the helpers' writes land in that table and nowhere else.)
  4. the helper object must not call any function that prints, reads stdin or looks at the clock (poll_input, gettime_ms, ob_*, print_*).
  5. self-test (a broken variant must be caught): the same audit on a helper object assembled from a source in which think writes bestmv in the helper flavour MUST fail.

usage: python3 tests/t_smp_audit.py [--bb 0|1]       exit code 0 = everything passed
"""
import os, re, subprocess, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
args = sys.argv[1:]
BB = args[args.index("--bb") + 1] if "--bb" in args else "1"
N = 4

# read-only tables of unit 0 that the search code takes the address of
READONLY = set("""lmrtab mgt egt zobp zobc zobe zobs cmask phasew pvalue atab rtab btab knight_att king_att patt_to to7 k_nfa k_nfh k_r1 k_r3 k_r6 k_r8 k_nr1 k_nr8
knightoff kingoff bishoff rookoff ptab pcnt smoves evp ev_k55 ev_k33 ev_k0f ev_k01 ev_filea ev_outr ev_hw ev_dark ev_qside ev_kside kpk_bb mateg ev_r23 ev_r5 ev_r6 ev_r7 ev_center ev_space ev_dtab""".split())
FORBIDDEN_CALLS = ("poll_input", "gettime_ms", "ob_ch", "ob_flush", "ob_move", "ob_num", "ob_str", "smp_", "check_time_main")
# instructions that only READ their first (memory) operand
READ_FIRST = {"cmp", "test", "bt", "push", "jmp", "call", "ucomisd", "ucomiss", "prefetcht0", "prefetchnta"}

def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode: print("FAILED:", " ".join(cmd), "\n", r.stdout[-800:], r.stderr[-1500:]); sys.exit(2)
    return r.stdout

def assemble(tmp, srcdir, k, name):
    asm_path = os.path.join(tmp, name + ".asm")
    parts = ["p1_data.asm", "eval.asm", "p2_core.asm", "p2b_bb_data.inc", "p2b_bb.asm", "p3_cmd.asm", "p4_search.asm", "p5_main.asm", "p6_win.asm", "p7_units.asm"]
    with open(asm_path, "wb") as o:
        for p in parts: o.write(open(os.path.join(srcdir, p), "rb").read())
    ob = os.path.join(tmp, f"{name}_u{k}.o")
    sh(["nasm", "-f", "elf64", f"-DUNIT={k}", f"-DNUNITS={N}", f"-DBB={BB}"] + [asm_path, "-o", ob])
    return ob

def symbols(ob):
    """(undefined names, {section: [(offset, name)]} of defined symbols)"""
    und = set(); defs = {}
    for l in sh(["nm", "-n", ob]).splitlines():
        w = l.split()
        if len(w) == 2 and w[0] == "U": und.add(w[1]); continue
        if len(w) != 3: continue
        defs.setdefault(w[1].upper(), []).append((int(w[0], 16), w[2]))
    return und, defs

def tlb_range(defs, k):
    lo = hi = None
    for a, n in defs.get("B", []):
        if n == f"tlb_{k}": lo = a
        if n == "tlb_end": hi = a
    return lo, hi

def audit(ob, k):
    """returns (problems, stats)"""
    und, defs = symbols(ob)
    lo, hi = tlb_range(defs, k)
    assert lo is not None and hi is not None, "state block labels not found"
    dis = sh(["objdump", "-dr", "-M", "intel", "--no-show-raw-insn", ob])
    prob = []; st = dict(reloc_insns=0, writes_own=0, reads_shared=0, addr_shared=set(), calls=set())
    ins = None
    for l in dis.splitlines():
        m = re.match(r"\s*([0-9a-f]+):\s+(.*\S)\s*$", l)
        if m and "R_X86_64" not in l:
            ins = m.group(2); continue
        r = re.match(r"\s*[0-9a-f]+:\s+R_X86_64_(\w+)\s+(\S+)", l)
        if not r or ins is None: continue
        rtype, target = r.groups(); st["reloc_insns"] += 1
        sym, _, add = target.partition("+")
        add = int(add, 16) if add else 0
        if rtype in ("PC32", "PLT32"):                      # call / jmp to a function
            mn = ins.split()[0]
            if mn in ("call", "jmp") or mn.startswith("j"):
                st["calls"].add(sym)
                if sym in und and any(sym.startswith(f) for f in FORBIDDEN_CALLS): prob.append(f"calls {sym}: {ins}")
            continue
        words = ins.replace("lock ", "lock_").replace("rep ", "rep_").split()
        mn = words[0]
        if mn.startswith("lock_") or mn.startswith("rep_"): mn = mn.split("_", 1)[1] if mn.startswith("rep_") else "lock"
        ops = ins.split(None, 1)[1] if len(ins.split(None, 1)) > 1 else ""
        first = ops.split(",")[0]
        is_mem_first = ("PTR" in first) or ("[" in first)
        mem_any = ("PTR" in ops) or ("[" in ops)
        if sym == ".bss" or sym == ".data" or sym == ".text":
            own = (sym == ".bss" and lo <= add < hi)
            target_name = f"{sym}+{add:#x}" + ("" if own else " (outside the state block)")
        else:
            own = False
            target_name = sym
        write = False
        if is_mem_first and mn not in READ_FIRST: write = True
        if mn.startswith("lock") or mn in ("xchg", "xadd", "cmpxchg"): write = True if mem_any else write
        if write:
            if own: st["writes_own"] += 1
            else: prob.append(f"WRITE to {target_name}: {ins}")
        elif not mem_any:                                   # the address of something is taken (mov reg, imm32 / lea)
            if sym in und:
                st["addr_shared"].add(sym)
                if sym not in READONLY: prob.append(f"address of {sym} taken (not a known read-only table): {ins}")
            elif sym in (".bss", ".data") and not own and sym == ".bss":
                prob.append(f"address of local data outside the state block taken: {target_name}: {ins}")
        else:
            if sym in und: st["reads_shared"] += 1
    return prob, st

def main():
    bad = 0
    def check(ok, msg):
        nonlocal bad
        print(("PASS " if ok else "FAIL ") + msg)
        if not ok: bad += 1
    tmp = tempfile.mkdtemp(prefix="smpaudit_")
    try:
        src = os.path.join(ROOT, "src")
        u1 = assemble(tmp, src, 1, "rel")
        prob, st = audit(u1, 1)
        for p in prob[:20]: print("   ", p)
        check(not prob, f"helper unit 1: {st['reloc_insns']} instructions name a symbol / section offset; writes to its own state block {st['writes_own']}; "
                        f"reads of shared variables {st['reads_shared']}; addresses of shared tables taken: {', '.join(sorted(st['addr_shared'])) or 'none'}; "
                        f"writes to anything shared: {len(prob)}")
        und, defs = symbols(u1)
        check(not any(n in und for n in ("poll_input", "gettime_ms", "ob_flush", "ob_ch", "ob_num")),
              "helper unit 1 does not even reference poll_input / gettime_ms / ob_* (no stdin, no clock, no output in the helper flavour)")
        # the main thread object must still contain them (sanity: the audit looks at the right thing)
        u0 = assemble(tmp, src, 0, "rel")
        und0, defs0 = symbols(u0)
        check(any(n == "poll_input" for a, n in defs0.get("T", [])), "unit 0 defines poll_input (the audit looks at the flavours it should)")
        # ---- self-test (a broken variant must be caught): a helper that writes bestmv must be caught
        bad_src = os.path.join(tmp, "src_bad"); shutil.copytree(src, bad_src)
        p4 = os.path.join(bad_src, "p4_search.asm"); s = open(p4).read()
        old = ".done:\n%if HELPER == 0\n"
        assert s.count(old) == 1
        s = s.replace(old, ".done:\n%if HELPER\n    mov dword [bestmv], 1\n%endif\n%if HELPER == 0\n"); open(p4, "w").write(s)
        ub = assemble(tmp, bad_src, 1, "bad")
        prob2, _ = audit(ub, 1)
        check(any("bestmv" in p for p in prob2), f"self-test: a helper that writes bestmv is caught ({len(prob2)} problem(s))")
        bad_src2 = os.path.join(tmp, "src_bad2"); shutil.copytree(src, bad_src2)
        p4 = os.path.join(bad_src2, "p4_search.asm"); s = open(p4).read()
        old = "%if HELPER == 0\n    mov dword [stopflag], 0\n%endif\n"
        assert s.count(old) == 1
        s = s.replace(old, "    mov dword [stopflag], 0\n"); open(p4, "w").write(s)
        ub2 = assemble(tmp, bad_src2, 1, "bad2")
        prob3, _ = audit(ub2, 1)
        check(any("stopflag" in p for p in prob3), f"self-test: a helper that clears stopflag is caught ({len(prob3)} problem(s))")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASSED" if not bad else f"{bad} FAILED")
    sys.exit(1 if bad else 0)

main()
