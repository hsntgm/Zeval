#!/usr/bin/env python3
"""Test helper (Lazy SMP): runs the other test scripts with more than one search thread without changing them.
usage:  ENGINE="python3 tests/threads_proxy.py 4 ./zeval" python3 tests/t_uci.py        (4 = value of the UCI option Threads)
It starts the engine, passes stdin / stdout through line by line, and inserts "setoption name Threads value N" right after every "uci" line."""
import subprocess, sys, threading, os
if len(sys.argv) < 3: sys.exit("usage: threads_proxy.py N ENGINE [engine args ...]   (see the docstring)")
n, exe = sys.argv[1], sys.argv[2:]
p = subprocess.Popen(exe, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
def pump():
    while True:
        d = os.read(p.stdout.fileno(), 65536)
        if not d: break
        os.write(1, d)
threading.Thread(target=pump, daemon=True).start()
buf = b""
try:
    while True:
        d = os.read(0, 65536)
        if not d: break
        buf += d
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            p.stdin.write(line + b"\n")
            if line.strip() == b"uci": p.stdin.write(b"setoption name Threads value " + n.encode() + b"\n")
            p.stdin.flush()
            if line.strip() == b"quit": raise EOFError           # the engine ends by itself: the proxy ends with it (rc passed on)
except EOFError: pass
except (BrokenPipeError, OSError): pass
try: p.stdin.close()
except Exception: pass
rc = p.wait()
import time; time.sleep(0.05)                                   # let the pump thread copy the last output
sys.exit(rc)
