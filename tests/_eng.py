"""Shared helper for the test scripts: which engine binary to run, a portable pipe reader, and hang protection.

Engine selection:  environment variable ENGINE  (default: zeval.exe on Windows, ./zeval elsewhere).
It may be a path (spaces are fine) or a command line such as "wine zeval.exe".
select() does not work on pipes under Windows, so output is read by a small thread (PipeReader).

Hang protection.  A test script that waits for "uciok" / "readyok" / "bestmove" with a bare  while readline() != X: pass  loop spins
forever when the engine crashes (readline() returns "" at EOF indefinitely) and blocks forever when the engine stays silent.  expect() and
LineReader() handle both cases:
  * EOF (crash, wrong path, not a UCI engine)      -> EngineGone
  * no answer within the timeout                    -> the process is killed, EngineGone
An EngineGone (or a BrokenPipeError on the engine's stdin) that nobody catches ends the script with ONE line "ERROR: ..." on stderr and exit code 2
(not a traceback; 1 stays "a check failed").
"""
import os, shlex, sys, threading, queue, time

def resolve(s):
    return [s] if os.path.exists(s) else shlex.split(s, posix=(os.name != "nt"))

ENGINE = resolve(os.environ.get("ENGINE", "zeval.exe" if os.name == "nt" else "./zeval"))


class EngineGone(Exception):
    """the engine closed its output (crash / wrong path / not a UCI engine) or did not answer in time (and was killed)"""


def _excepthook(t, v, tb):
    if issubclass(t, (EngineGone, BrokenPipeError)):
        try: sys.stdout.flush()
        except Exception: pass
        msg = v if issubclass(t, EngineGone) else "broken pipe to the engine: it exited or crashed before the script was done with it"
        sys.stderr.write("ERROR: %s\n" % msg); sys.stderr.flush()
        os._exit(2)                       # 2 = the harness could not talk to the engine; 1 = a check failed
    sys.__excepthook__(t, v, tb)

sys.excepthook = _excepthook


class PipeReader:
    """Reads a subprocess pipe in a thread; get(timeout) returns bytes, b'' at EOF, None on timeout."""
    def __init__(self, fileobj):
        self.fd = fileobj.fileno(); self.q = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
    def _pump(self):
        while True:
            try: d = os.read(self.fd, 65536)
            except OSError: d = b""
            self.q.put(d)
            if not d: break
    def get(self, timeout):
        try: return self.q.get(timeout=timeout)
        except queue.Empty: return None


class LineReader:
    """Line reader with a timeout on top of PipeReader (for engines opened WITHOUT text=True / with their own stdin writes).
    get(timeout) -> one line (str, no line end) | None on timeout; raises EngineGone at EOF (after the last complete line)."""
    def __init__(self, fileobj):
        self.rd = PipeReader(fileobj); self.buf = b""; self.eof = False
    def get(self, timeout):
        end = time.time() + timeout
        while b"\n" not in self.buf:
            if self.eof:
                if self.buf:
                    l, self.buf = self.buf, b""; return l.decode(errors="replace").rstrip("\r")
                raise EngineGone("engine closed its output (crash, wrong path, not a UCI engine?)")
            left = end - time.time()
            if left <= 0: return None
            d = self.rd.get(left)
            if d is None: return None
            if not d: self.eof = True
            else: self.buf += d
        l, self.buf = self.buf.split(b"\n", 1)
        return l.decode(errors="replace").rstrip("\r")


_readers = {}

def _reader_of(p):
    """one LineReader per process (created on first use; it owns the pipe from then on: do not call p.stdout.readline() yourself afterwards)"""
    r = _readers.get(id(p))
    if r is None or r[0] is not p:
        r = (p, LineReader(p.stdout)); _readers[id(p)] = r
    return r[1]


def start(cmd, **kw):
    """subprocess.Popen with stdin / stdout pipes (text mode, line buffered, stderr discarded) that turns 'cannot start it' into EngineGone"""
    import subprocess
    kw.setdefault("stdin", subprocess.PIPE); kw.setdefault("stdout", subprocess.PIPE); kw.setdefault("stderr", subprocess.DEVNULL)
    kw.setdefault("text", True); kw.setdefault("bufsize", 1)
    try: return subprocess.Popen(cmd, **kw)
    except OSError as e: raise EngineGone("cannot start %r: %s" % (cmd, e))


def expect(p, want, timeout=120.0, collect=None, what="engine"):
    """Read the engine's output until a line starts with `want` (a string) or want(line) is true (a callable).  Returns that line (stripped).
    Every line read is appended to `collect` (a list) when given.  p = a Popen with stdout=PIPE (use start()); text mode or bytes both work.
    EOF -> EngineGone.  No answer within `timeout` seconds -> the process is killed and EngineGone is raised (also when the engine is a wrapper
    such as "wine zeval.exe": the wait is a timed queue read, it cannot block)."""
    test = (lambda l: l.startswith(want)) if isinstance(want, str) else want
    tag = (" while waiting for %r" % want) if isinstance(want, str) else ""
    lr = _reader_of(p); end = time.time() + timeout
    while True:
        try: line = lr.get(max(0.0, end - time.time()))
        except EngineGone:
            raise EngineGone("%s closed its output (crash, wrong path, not a UCI engine?) rc=%s%s" % (what, p.poll(), tag))
        if line is None:
            try: p.kill()
            except OSError: pass
            raise EngineGone("%s gave no answer within %g s%s (process killed)" % (what, timeout, tag))
        line = line.strip()
        if collect is not None: collect.append(line)
        if test(line): return line


def finish(p, timeout=10):
    """send nothing, just make sure the process is gone: wait, then kill.  Returns the exit code (-9 if it had to be killed)."""
    try: return p.wait(timeout=timeout)
    except Exception:
        try: p.kill()
        except OSError: pass
        try: p.wait(timeout=5)
        except Exception: pass
        return -9
