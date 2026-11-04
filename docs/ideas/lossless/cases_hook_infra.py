"""Hook and serve infrastructure ideas: #459 #834 #838 #839 #842 #870 #1195 #1198 #1199 #1378.

These are mechanisms, not text transforms, so every case is a `sim`: a deterministic simulation with fakes (fake host
and clock, fake scorer, fake filesystem) that raises AssertionError when a property fails. Where the idea is about code
that already exists in ctxprune (hook.run, Compressor.compress, compress._hub_file, atoms.is_protected) the real code
is the oracle and only the model is faked (the real Compressor.compress runs on a deterministic fake atom scorer).
"""
import array
import contextlib
import fcntl
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
import math
import mmap
import os
import random
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import zlib

from harness import Fixture, case  # noqa: F401  (Fixture unused: every case here is a sim)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _ctx():
    """The real ctxprune package (stdlib-only parts import offline; onnxruntime and the hub are never imported)."""
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import ctxprune.atoms
    import ctxprune.compress
    import ctxprune.hook

    return ctxprune


def _fake_compressor(salt: str = "s"):
    """The REAL Compressor.compress (budget loop, force_protected, detok) driven by a deterministic fake atom scorer."""
    ctx = _ctx()
    c = ctx.compress.Compressor.__new__(ctx.compress.Compressor)

    def atom_scores(self, text):
        atoms = ctx.atoms.atomize(text)
        scores = [(zlib.crc32(f"{salt}{i}|{a.text}".encode()) % 10007) / 10007 for i, a in enumerate(atoms)]
        return atoms, scores, [1] * len(atoms)

    c.atom_scores = types.MethodType(atom_scores, c)
    return c


@contextlib.contextmanager
def _env(**kw):
    old = {k: os.environ.get(k) for k in kw}
    try:
        for k, v in kw.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class FakeClock:
    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


# ===============================================================================================================
# #459 Self-spawn a detached serve on first large output.
#
# Mechanism under test: single-flight by an flock the DAEMON inherits (pass_fds), so "lock held" == "a daemon is alive or
# starting" and the kernel releases it when the daemon or a crashed spawner dies (no stale-lock timeout, no unlink race).
# Hook processes are generators that yield at every point where another process may run; a seeded discrete-event
# scheduler explores interleavings on a fake clock, and a real-thread run checks the same code under OS scheduling.

FAKE_SERVE_ARGV = [sys.executable, "-m", "ctxprune", "serve", "--idle-exit", "600"]  # --idle-exit is the new flag the idea needs


def _fake_compress(text: str) -> str:
    """Stand-in for the model: deterministic, so every path (daemon or in process) must return exactly this."""
    return " ".join(w for i, w in enumerate(text.split(" ")) if i % 2 == 0)


class Host:
    """The machine as the hook sees it: a clock, TCP port 8719, and child processes. No real sockets or processes."""

    def __init__(self, clock, lockdir, load_s=2.0, idle_s=600.0, foreign=False, spawn_error=False, die_on_start=False):
        self.now, self.lockdir = clock, lockdir
        self.load_s, self.idle_s, self.foreign = load_s, idle_s, foreign
        self.spawn_error, self.die_on_start = spawn_error, die_on_start
        self.spawns, self.probes, self.exits, self.served = [], 0, 0, 0
        self.model_loads = 0  # anywhere: the daemon's one load plus every in-process fallback
        self.live_loads = 0   # in-process loads (each is 143 MB of private RSS)
        self.daemon = None
        self._mu = threading.Lock()

    # --- lock + marker files (real files, real flock) ---
    @property
    def lock_path(self):
        return os.path.join(self.lockdir, "serve.lock")

    @property
    def failed_path(self):
        return os.path.join(self.lockdir, "serve.failed")

    def _tick(self):
        d = self.daemon
        if d is None:
            return
        t = self.now()
        if not d["up"] and t >= d["ready_at"]:
            if self.die_on_start:  # crashed before binding the port
                self._exit()
                return
            d["up"], d["last_used"] = True, t
        if d["up"] and t - d["last_used"] > self.idle_s:
            self._exit()

    def _exit(self):
        os.close(self.daemon["fd"])  # the daemon's copy of the lock fd: lock is released
        self.daemon = None
        self.exits += 1

    def probe(self):
        """'ctxprune' (GET /health answered by us), 'foreign' (something else owns the port) or None (closed)."""
        with self._mu:
            self.probes += 1
            self._tick()
            if self.daemon and self.daemon["up"]:
                return "ctxprune"
            return "foreign" if self.foreign else None

    def request(self, text):
        with self._mu:
            self._tick()
            if not (self.daemon and self.daemon["up"]):
                return None  # went away between probe and request
            self.daemon["last_used"] = self.now()
            self.served += 1
            return _fake_compress(text)

    def spawn(self, argv, **kw):
        with self._mu:
            if self.spawn_error:
                raise OSError("fork failed")
            self.spawns.append((argv, kw))
            fd = os.dup(kw["pass_fds"][0])  # what the child inherits: the same open file description
            self.daemon = {"ready_at": self.now() + self.load_s, "up": False, "fd": fd, "last_used": 0.0}
            self.model_loads += 1

    def lock_free(self):
        """True if nobody holds the lock (probe-and-release)."""
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
        finally:
            os.close(fd)

    def in_process_load(self):
        with self._mu:
            self.model_loads += 1
            self.live_loads += 1


STEP = ("step",)


def _cooldown_active(host, cooldown_s):
    try:
        return host.now() - float(open(host.failed_path).read()) < cooldown_s
    except (OSError, ValueError):
        return False  # no marker, or a corrupt one: not in cooldown


def _mark_failed(host):
    try:
        with open(host.failed_path, "w") as f:
            f.write(str(host.now()))
    except OSError:
        pass


def hook_call(host, text, out, server="http://127.0.0.1:8719", min_chars=6000, wait_s=10.0, poll_s=0.25, cooldown_s=300.0):
    """One PostToolUse hook process, as a generator. Fills `out` with how the text was compressed. Never raises."""
    if len(text) < min_chars:
        out["path"] = "small"  # sessions with no large outputs pay nothing: no probe, no lock, no spawn
        return
    local = server.startswith(("http://127.0.0.1", "http://localhost"))
    if server and local:
        for _attempt in range(2):
            yield STEP
            st = host.probe()
            if st == "ctxprune":
                r = host.request(text)
                if r is not None:
                    out.update(path="daemon", text=r)
                    return
            if st == "foreign" or _cooldown_active(host, cooldown_s):
                break  # port owned by something else (cannot bind), or a recent spawn failed: do not retry per call
            yield STEP
            fd = os.open(host.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                won = True
            except BlockingIOError:
                won = False
            try:
                if won:
                    yield STEP
                    try:
                        host.spawn(FAKE_SERVE_ARGV, start_new_session=True, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, pass_fds=(fd,))
                    except OSError:
                        _mark_failed(host)
                        break  # fail open: compress in this process
            finally:
                os.close(fd)  # the daemon holds its own copy, so the lock stays held while it lives
            deadline = host.now() + wait_s
            daemon_died = False
            while host.now() < deadline:
                yield ("sleep", poll_s)
                st = host.probe()
                if st == "ctxprune":
                    r = host.request(text)
                    if r is not None:
                        out.update(path="daemon-after-wait", text=r)
                        return
                if st == "foreign":
                    break
                if host.lock_free():  # nobody holds it and the port is closed: the daemon died
                    daemon_died = True
                    break
            if daemon_died:
                _mark_failed(host)
            break
    host.in_process_load()
    out.update(path="in-process", text=_fake_compress(text))


def _run_coop(clock, gens, seed, kill=None):
    """Seeded discrete-event scheduler. kill=(i, n): process i is killed (generator closed, so its finally blocks run,
    as the kernel would close its fds) just before its (n+1)th step."""
    rng = random.Random(seed)
    wake = {i: clock.t for i in range(len(gens))}
    steps = dict.fromkeys(wake, 0)
    killed = set()
    for _ in range(200000):
        if not wake:
            return killed
        runnable = sorted(i for i, w in wake.items() if w <= clock.t)
        if not runnable:
            clock.t = min(wake.values())
            continue
        i = rng.choice(runnable)
        if kill and kill[0] == i and steps[i] == kill[1]:
            gens[i].close()
            killed.add(i)
            del wake[i]
            continue
        try:
            ev = next(gens[i])
        except StopIteration:
            del wake[i]
            continue
        steps[i] += 1
        wake[i] = clock.t + (ev[1] if ev[0] == "sleep" else 0.0)
    raise AssertionError("scheduler livelock: processes never finished")


BIG = ("the build step finished with a warning about the cache directory being slow " * 120)


def sim_selfspawn() -> dict:
    stats = {}
    # (a) saving: 4 parallel first calls, no server. Seeded interleavings on a fake clock.
    for seed in range(80):
        with tempfile.TemporaryDirectory() as d:
            clock = FakeClock()
            host = Host(clock, d, load_s=2.0)
            outs = [{} for _ in range(4)]
            _run_coop(clock, [hook_call(host, BIG, o) for o in outs], seed)
            assert len(host.spawns) == 1, f"seed {seed}: {len(host.spawns)} spawns for 4 concurrent first calls"
            assert host.model_loads == 1 and host.live_loads == 0, f"seed {seed}: model loaded {host.model_loads}x"
            assert all(o["text"] == _fake_compress(BIG) for o in outs), "reader view: every caller gets the model's output"
            argv, kw = host.spawns[0]
            assert kw["start_new_session"] and kw["stdin"] == kw["stdout"] == kw["stderr"] == subprocess.DEVNULL \
                and kw["close_fds"], "daemon must be fully detached from the hook's session and stdio"
    stats["model_loads_4_parallel_first_calls"] = 1
    stats["model_loads_without_daemon"] = 4

    # real threads and the real flock, many rounds: at most one spawn, correct output, always terminates
    for rnd in range(25):
        with tempfile.TemporaryDirectory() as d:
            host = Host(time.monotonic, d, load_s=0.0)
            outs = [{} for _ in range(8)]
            barrier = threading.Barrier(8)

            def worker(o):
                barrier.wait()
                for ev in hook_call(host, BIG, o, wait_s=20.0):
                    time.sleep(0.0005 if ev[0] == "sleep" else 0)

            ts = [threading.Thread(target=worker, args=(o,)) for o in outs]
            [t.start() for t in ts]
            [t.join(30) for t in ts]
            assert all(not t.is_alive() for t in ts), "a hook thread hung"
            assert len(host.spawns) <= 1, f"thread round {rnd}: {len(host.spawns)} spawns"
            assert all(o.get("text") == _fake_compress(BIG) for o in outs), "wrong or missing output under real threads"

    # (a2) later calls hit the daemon (no load, no spawn); it exits after the idle timeout; the next call respawns once
    with tempfile.TemporaryDirectory() as d:
        clock = FakeClock()
        host = Host(clock, d, load_s=2.0, idle_s=600.0)
        o = {}
        _run_coop(clock, [hook_call(host, BIG, o)], 0)
        assert o["path"] == "daemon-after-wait" and len(host.spawns) == 1
        for k in range(10):
            clock.t += 30
            o = {}
            _run_coop(clock, [hook_call(host, BIG, o)], k)
            assert o["path"] == "daemon" and o["text"] == _fake_compress(BIG)
        assert len(host.spawns) == 1 and host.model_loads == 1 and host.served == 11
        clock.t += 601  # idle past the timeout: the daemon exits and its lock is released
        host.probe()
        assert host.daemon is None and host.exits == 1 and host.lock_free(), "daemon must exit when idle and free the lock"
        o = {}
        _run_coop(clock, [hook_call(host, BIG, o)], 5)
        assert len(host.spawns) == 2 and o["text"] == _fake_compress(BIG), "after idle exit the next large call respawns once"

    # (c) must NOT apply
    with tempfile.TemporaryDirectory() as d:  # small outputs: zero cost, nothing touched
        clock = FakeClock()
        host = Host(clock, d)
        for _ in range(1000):
            o = {}
            _run_coop(clock, [hook_call(host, "ok\n" * 100, o)], 0)
            assert o["path"] == "small"
        assert host.probes == 0 and not host.spawns and not os.path.exists(host.lock_path), "small outputs must be free"
    for server in ("", "http://build-box.example:8719"):  # server disabled by the user, or a remote one: never self-spawn
        with tempfile.TemporaryDirectory() as d:
            host = Host(FakeClock(), d)
            o = {}
            _run_coop(host.now, [hook_call(host, BIG, o, server=server)], 0)
            assert not host.spawns and o["path"] == "in-process" and o["text"] == _fake_compress(BIG)
    with tempfile.TemporaryDirectory() as d:  # another service owns the port: a spawn could not bind it
        host = Host(FakeClock(), d, foreign=True)
        o = {}
        _run_coop(host.now, [hook_call(host, BIG, o)], 0)
        assert not host.spawns and o["path"] == "in-process" and o["text"] == _fake_compress(BIG)

    # (d) failure handling: fail open, never stuck, no spawn storm
    with tempfile.TemporaryDirectory() as d:  # fork fails
        clock = FakeClock()
        host = Host(clock, d, spawn_error=True)
        for k in range(10):
            clock.t += 1
            o = {}
            _run_coop(clock, [hook_call(host, BIG, o)], k)
            assert o["path"] == "in-process" and o["text"] == _fake_compress(BIG)
        assert host.live_loads == 10 and not host.spawns
        host.spawn_error = False
        clock.t += 301  # cooldown over: tries again
        o = {}
        _run_coop(clock, [hook_call(host, BIG, o)], 0)
        assert len(host.spawns) == 1 and o["text"] == _fake_compress(BIG)
    with tempfile.TemporaryDirectory() as d:  # daemon spawns but crashes on start (broken install): one attempt per cooldown
        clock = FakeClock()
        host = Host(clock, d, die_on_start=True)
        for k in range(10):
            clock.t += 1
            o = {}
            _run_coop(clock, [hook_call(host, BIG, o)], k)
            assert o["path"] == "in-process" and o["text"] == _fake_compress(BIG)
        assert len(host.spawns) == 1, f"crash-looping daemon was respawned {len(host.spawns)}x in 10 s"
        stats["spawns_for_10_calls_with_crashing_daemon"] = len(host.spawns)
    for crash_step in range(0, 4):  # the would-be spawner is killed at every step: lock must not stay stuck
        for seed in range(15):
            with tempfile.TemporaryDirectory() as d:
                clock = FakeClock()
                host = Host(clock, d, load_s=2.0)
                outs = [{} for _ in range(3)]
                killed = _run_coop(clock, [hook_call(host, BIG, o) for o in outs], seed, kill=(0, crash_step))
                assert len(host.spawns) <= 1, "crash must not cause a second daemon"
                for i, o in enumerate(outs):
                    if i not in killed:
                        assert o.get("text") == _fake_compress(BIG), f"survivor {i} got nothing (crash at step {crash_step})"
                if not host.spawns:  # the crashed process never spawned: lock must be free for the next caller
                    assert host.lock_free(), "lock stuck after a crashed spawner"
    stats["interleavings_checked"] = 80 + 25 + 60
    return stats


case(
    459,
    claim="On the first over-threshold call a hook process spawns one fully detached serve under an flock the daemon inherits: 4 concurrent first calls give exactly 1 spawn and 1 model load, every caller receives the same output, later calls reuse the daemon, it exits after the idle timeout, and spawn failure, a crashing daemon, a foreign port owner, a disabled or remote server and a crashed spawner all fall back to in-process compression without a stuck lock or a spawn storm.",
    sim=sim_selfspawn,
)

# ===============================================================================================================
# #834 Skip the model load when the protected share forces a bail-out.
#
# Soundness argument under test. With force_protected the output keeps every protected atom's text verbatim AND the
# whitespace in front of it whenever its predecessor is also kept (always true when the predecessor is protected), so
#     len(output) >= LB = sum(len(atom) + ws(atom) for protected atoms) - ws(first protected atom)
# where ws(atom) is the atom's own leading whitespace after a protected neighbour, else 1 if it has any (a dropped neighbour
# leaves one separator). hook.run bails when after >= 0.95 * before, so LB >= 0.95 * before forces the bail whatever the model
# scores. Two weaker variants are compared: the idea's literal protected-CHARACTER share (ignores the whitespace between
# protected atoms, so `seq` never reaches it) and the idea's `rate >= 0.97` arm, which is NOT implied (rate is in atom units,
# the bail test in characters) and is refuted by a counterexample.

BAIL = 0.95


def _atoms_protected(text):
    ctx = _ctx()
    atoms = ctx.atoms.atomize(text)
    return atoms, [bool(a.text) and ctx.atoms.is_protected(a.text) for a in atoms]


def _protected_chars(texts):
    n = 0
    for t in texts:
        atoms, prot = _atoms_protected(t)
        n += sum(len(a.text) for a, p in zip(atoms, prot) if p)
    return n


def output_lower_bound(texts):
    lb = 0
    for t in texts:
        atoms, prot = _atoms_protected(t)
        first_w, prev = None, False
        for a, p in zip(atoms, prot):
            if p:
                w = len(a.ws) if prev else (1 if a.ws else 0)
                lb += len(a.text) + w
                if first_w is None:
                    first_w = w
            prev = p
        lb -= first_w or 0
    return lb


def _event_texts(hook, event, min_chars):
    """The strings hook.run would compress: reuse its own slot logic."""
    tool, resp = event.get("tool_name", ""), event.get("tool_response")
    if isinstance(resp, str):
        return [resp] if len(resp) >= min_chars else []
    return [c[k] for c, k in hook._text_slots(tool, resp) if len(c[k]) >= min_chars]


def prefiltered_run(hook, event, rate, min_chars, mode):
    """Shortcut in front of hook.run. mode 'lb': skip when the whitespace-aware lower bound >= 0.95*before (sound);
    'chars': literal protected-character share >= 0.95 (sound, weaker); 'idea': max(rate, chars share) >= 0.97 as written.
    Any error inside the shortcut falls through to the full path."""
    try:
        texts = _event_texts(hook, event, min_chars)
        if texts:
            before = sum(map(len, texts))
            if mode == "lb" and output_lower_bound(texts) >= BAIL * before:
                return None
            if mode == "chars" and _protected_chars(texts) >= BAIL * before:
                return None
            if mode == "idea" and max(rate, _protected_chars(texts) / before) >= 0.97:
                return None
    except Exception:  # noqa: BLE001  fail open: the shortcut must never change behaviour
        pass
    return hook.run(event, rate, min_chars)


def _env_dump(n, rng):
    return "".join(f"{k}={v}\n" for k, v in (
        (f"{rng.choice(['PATH', 'LD_LIBRARY_PATH', 'PYTHONPATH', 'XDG_DATA_DIRS'])}_{i}",
         ":".join(f"/usr/lib/x86_64-linux-gnu/{rng.randrange(99)}/bin" for _ in range(3))) for i in range(n)))


def _ls_lR(n, rng):
    out = []
    for d in range(max(1, n // 8)):
        out.append(f"\n./src/pkg{d}:\ntotal {rng.randrange(900)}\n")
        for f in range(8):
            out.append(f"-rw-r--r-- 1 root root {rng.randrange(10**6):7d} Nov  4 10:{rng.randrange(60):02d} file_{d}_{f}.py\n")
    return "".join(out)


def _numeric_table(n, rng):
    return "id   count   total   mean\n" + "".join(f"{i:5d} {rng.randrange(10**4):7d} {rng.randrange(10**7):9d} {rng.random():.4f}\n" for i in range(n))


def _prose_log(n, rng):
    words = "the build step finished with a warning about the cache directory being slow so we retried it once".split()
    return "".join(" ".join(rng.choice(words) for _ in range(12)) + f" at step {i}\n" for i in range(n))


def _event(text, cmd="x"):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "tool_response": {"stdout": text, "stderr": ""}}


def sim_protected_bailout() -> dict:
    from harness import mutants

    ctx = _ctx()
    hook = ctx.hook
    rng = random.Random(834)
    fc = _fake_compressor("834")
    saved_compress = hook._compress
    hook._compress = lambda texts, rate: [fc.compress(t, rate=rate, force_protected=True)["text"] for t in texts]
    stats = {"events": 0, "bails_in_full_path": 0, "bails_caught_lb": 0, "bails_caught_chars_share": 0}
    try:
        with tempfile.TemporaryDirectory() as cache, _env(XDG_CACHE_HOME=cache):
            corpus = ([("env", _env_dump(n, rng)) for n in (40, 60)] + [("seq", "".join(f"{i}\n" for i in range(1, 900)))]
                      + [("ls -lR", _ls_lR(n, rng)) for n in (48, 64)] + [("table", _numeric_table(n, rng)) for n in (80, 120)]
                      + [("prose", _prose_log(n, rng)) for n in (40, 60)]
                      + [("mixed", _prose_log(10, rng) + _numeric_table(150, rng))])
            for rate in (0.3, 0.5, 0.9):
                for name, text in corpus:
                    ev = _event(text, name)
                    full = hook.run(ev, rate, 200)
                    for mode in ("lb", "chars"):
                        got = prefiltered_run(hook, ev, rate, 200, mode)
                        assert got == full, f"{name} rate={rate} mode={mode}: shortcut changed the hook result"
                    skipped = {m: output_lower_bound([text]) >= BAIL * len(text) if m == "lb" else _protected_chars([text]) >= BAIL * len(text)
                               for m in ("lb", "chars")}
                    assert not (skipped["lb"] and full is not None), f"false skip on {name} (lb)"
                    assert not (skipped["chars"] and full is not None), f"false skip on {name} (chars)"
                    assert not (skipped["chars"] and not skipped["lb"]), "lb bound must dominate the char share"
                    stats["events"] += 1
                    stats["bails_in_full_path"] += full is None
                    stats["bails_caught_lb"] += skipped["lb"] and full is None
                    stats["bails_caught_chars_share"] += skipped["chars"] and full is None
            assert stats["bails_caught_lb"] > stats["bails_caught_chars_share"] >= 3, "whitespace-aware bound catches strictly more (seq)"
            seq = next(t for n, t in corpus if n == "seq")
            assert _protected_chars([seq]) < BAIL * len(seq) <= output_lower_bound([seq]), "seq: literal share misses it, bound catches it"
            prose = next(t for n, t in corpus if n == "prose")
            assert output_lower_bound([prose]) < BAIL * len(prose) and hook.run(_event(prose), 0.5, 200) is not None, \
                "must NOT skip prose: the full path compresses it"
            lsr = next(t for n, t in corpus if n == "ls -lR")
            assert hook.run(_event(lsr), 0.5, 200) is not None and output_lower_bound([lsr]) < BAIL * len(lsr), "ls -lR is compressed, not skipped"

            # the bound itself, against the real Compressor.compress: mutated inputs (CRLF, tabs, unicode, truncation), several scorers
            holds = 0
            small = [_env_dump(10, rng), "".join(f"{i}\n" for i in range(1, 150)), _ls_lR(16, rng), _numeric_table(20, rng),
                     _prose_log(10, rng), "  \n\t leading ws\r\n 12  34\r\n\r\nabc 5\n"]
            for ti, t in enumerate(small):
                for m in mutants(t, 12, seed=834 + ti):
                    for salt in ("a", "b"):
                        f2 = _fake_compressor(salt)
                        for rate in (0.0, 0.5, 1.0):
                            out = f2.compress(m, rate=rate, force_protected=True)["text"]
                            lb = output_lower_bound([m])
                            assert lb <= len(out), f"bound {lb} exceeds real output {len(out)} for {m!r} rate={rate}"
                            holds += 1
            stats["bound_checks_against_real_compress"] = holds

            # the idea's literal `rate >= 0.97` arm is unsound in characters: whitespace-heavy atoms the scorer drops first.
            heavy = "".join("\n" + " " * 40 + "dropme" + "\nk" * 200 for _ in range(6))
            fc_drop = _fake_compressor("834")

            def atom_scores(self, text):  # the scorer rates 'dropme' lowest
                atoms = ctx.atoms.atomize(text)
                return atoms, [0.0 if a.text == "dropme" else 1.0 for a in atoms], [1] * len(atoms)

            fc_drop.atom_scores = types.MethodType(atom_scores, fc_drop)
            hook._compress = lambda texts, rate: [fc_drop.compress(t, rate=rate, force_protected=True)["text"] for t in texts]
            ev = _event(heavy)
            full = hook.run(ev, 0.97, 200)
            assert full is not None, "counterexample must be a case where the full path does not bail"
            assert prefiltered_run(hook, ev, 0.97, 200, "idea") is None, "the idea's literal rate arm skips it: a false skip"
            assert prefiltered_run(hook, ev, 0.97, 200, "lb") == full, "the sound rule still returns the full result"
            stats["rate_arm_false_skip_counterexample"] = 1

            # fail open: if the shortcut itself raises, the full path runs and gives the same result
            hook._compress = lambda texts, rate: [fc.compress(t, rate=rate, force_protected=True)["text"] for t in texts]
            ev = _event(seq)
            orig, calls = globals()["output_lower_bound"], []

            def broken(texts):
                calls.append(1)
                raise RuntimeError("boom")

            globals()["output_lower_bound"] = broken
            try:
                assert prefiltered_run(hook, ev, 0.5, 200, "lb") == hook.run(ev, 0.5, 200), "fail open on prefilter error"
            finally:
                globals()["output_lower_bound"] = orig
            assert calls, "the prefilter failure path was exercised"
    finally:
        hook._compress = saved_compress
    return stats


case(
    834,
    claim="Skipping the model when a whitespace-aware lower bound on the output (protected atoms plus the whitespace they keep) is >= 95% of the input returns exactly what the full hook path returns (None) on env, seq, ls -lR, numeric-table and prose inputs, the bound holds against the real Compressor.compress on mutated inputs, the idea's rate>=0.97 arm is refuted by a counterexample, and a failing prefilter falls through to the full path.",
    sim=sim_protected_bailout,
)

# ===============================================================================================================
# #838 Load tokenizer and ORT session in parallel threads.
#
# Mechanism: the calling thread runs loaders[0] (tokenizer parse) while a worker runs loaders[1] (InferenceSession build);
# results come back in declared order, so the Compressor ends up with exactly the objects a serial load builds. The real
# loaders are native (tokenizers, onnxruntime) and release the GIL; time.sleep stands in for that. A pure-Python busy loop
# stands in for a loader that does NOT release it, to show what the wall-time check detects.


def load_serial(loaders):
    return tuple(f() for f in loaders)


def load_parallel(loaders, start_thread=threading.Thread):
    """loaders[0] runs on the calling thread, the others on workers. Results in declared order. Every worker is joined, then the
    first-declared failure is re-raised (the one a serial load would have raised). If a thread cannot be started the remaining
    loaders run inline, so the worst case is the serial path."""
    n = len(loaders)
    results, errors = [None] * n, [None] * n

    def run(i):
        try:
            results[i] = loaders[i]()
        except BaseException as e:  # noqa: BLE001  re-raised below, on the calling thread
            errors[i] = e

    workers, started = [], set()
    for i in range(1, n):
        try:
            t = start_thread(target=run, args=(i,), daemon=True)
            t.start()
        except RuntimeError:
            break  # "can't start new thread": fall back to inline for this and later loaders
        workers.append(t)
        started.add(i)
    for i in range(1, n):
        if i not in started:
            run(i)
    run(0)
    for t in workers:
        t.join()
    for e in errors:
        if e is not None:
            raise e
    return tuple(results)


def _stand_in_loaders(tok_s, sess_s, calls, tok_err=None, sess_err=None):
    def tok():
        calls.append("tok")
        time.sleep(tok_s)
        if tok_err:
            raise tok_err
        return {"vocab": 50265, "pad": 1, "ids": tuple(range(8))}

    def sess():
        calls.append("sess")
        time.sleep(sess_s)
        if sess_err:
            raise sess_err
        return ("session", hashlib.sha256(b"model_quantized.onnx").hexdigest())

    return [tok, sess]


class _FakeCompressor:
    """What Compressor.__init__ does with the two loaded objects; compress() depends on both, so a swapped or missing
    object changes the output."""

    def __init__(self, tok, sess):
        self.tok, self.sess = tok, sess

    def compress(self, text):
        salt = self.sess[1][:4] + str(self.tok["vocab"])
        return " ".join(w for w in text.split() if zlib.crc32((salt + w).encode()) % 3)


def _spin(n):
    x = 0
    for i in range(n):  # pure Python: holds the GIL for its whole duration
        x += i * i % 7
    return x


def sim_parallel_load() -> dict:
    sample = "the quick brown fox jumps over the lazy dog while the build step finished with a warning " * 5
    base_threads = threading.active_count()

    # (a) saving, and output unchanged. Both orders of which phase is slower.
    walls = {}
    for tok_s, sess_s in ((0.10, 0.10), (0.03, 0.10), (0.10, 0.03)):
        calls = []
        t0 = time.perf_counter()
        serial = load_serial(_stand_in_loaders(tok_s, sess_s, calls))
        walls["serial"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        par = load_parallel(_stand_in_loaders(tok_s, sess_s, calls))
        walls["parallel"] = time.perf_counter() - t0
        assert par == serial, "parallel load must hand back equal objects, in declared order"
        assert sorted(calls) == ["sess", "sess", "tok", "tok"], "each loader runs exactly once per load"
        assert _FakeCompressor(*par).compress(sample) == _FakeCompressor(*serial).compress(sample), "compression output unchanged"
        assert walls["parallel"] < walls["serial"] - 0.8 * min(tok_s, sess_s), \
            f"overlap missing: serial {walls['serial']:.3f}s parallel {walls['parallel']:.3f}s"
        if (tok_s, sess_s) == (0.10, 0.10):
            assert walls["parallel"] < 0.75 * walls["serial"], "wall time must fall well below the sum of the two phases"
            main = dict(walls)

    # the wall-time check from the idea: parallel wall time against the SUM of the phases timed alone
    def wall_over_sum(loaders):
        solo = []
        for f in loaders:
            t = time.perf_counter()
            f()
            solo.append(time.perf_counter() - t)
        t = time.perf_counter()
        load_parallel(list(loaders))
        return (time.perf_counter() - t) / sum(solo)

    releasing = wall_over_sum([lambda: time.sleep(0.06), lambda: time.sleep(0.06)])
    assert releasing < 0.75, f"phases that release the GIL must overlap (wall/sum={releasing:.2f})"
    if getattr(sys, "_is_gil_enabled", lambda: True)():
        t = time.perf_counter()
        _spin(200000)
        n = int(200000 * 0.04 / (time.perf_counter() - t))  # about 40 ms of pure-Python work per phase
        holding = wall_over_sum([lambda: _spin(n), lambda: _spin(n)])
        assert holding > 0.85, f"pure-Python phases cannot overlap under the GIL (wall/sum={holding:.2f}); the check flags them"

    # (c) must NOT apply: one loader means no thread at all
    started = []

    def counting_thread(**kw):
        started.append(1)
        return threading.Thread(**kw)

    assert load_parallel([lambda: 7], start_thread=counting_thread) == (7,) and not started

    # (d) failures propagate, are the same ones serial would raise, and leave no stray threads
    for tok_err, sess_err in ((None, OSError("corrupt onnx")), (ValueError("bad tokenizer.json"), None),
                              (ValueError("bad tokenizer.json"), OSError("corrupt onnx"))):
        want = None
        try:
            load_serial(_stand_in_loaders(0.01, 0.01, [], tok_err, sess_err))
        except Exception as e:  # noqa: BLE001
            want = e
        try:
            load_parallel(_stand_in_loaders(0.01, 0.01, [], tok_err, sess_err))
        except Exception as e:  # noqa: BLE001
            assert type(e) is type(want) and str(e) == str(want), f"raised {e!r}, serial raises {want!r}"
        else:
            raise AssertionError("failure in one loader was swallowed")
    assert threading.active_count() == base_threads, "worker threads must be joined, even on failure"

    def cannot_start(**kw):
        raise RuntimeError("can't start new thread")

    calls = []
    assert load_parallel(_stand_in_loaders(0.01, 0.01, calls), start_thread=cannot_start) == load_serial(_stand_in_loaders(0.01, 0.01, [])), \
        "thread exhaustion degrades to the serial path with the same result"
    assert sorted(calls) == ["sess", "tok"]
    return {"serial_s": round(main["serial"], 3), "parallel_s": round(main["parallel"], 3), "saved_s": round(main["serial"] - main["parallel"], 3),
            "wall_over_sum_releasing": round(releasing, 2)}


case(
    838,
    claim="Running two loaders on two threads returns equal objects in declared order and a Compressor with identical output, wall time drops below 0.75x the sum of the phases when they release the GIL (and the same check flags phases that do not), and a failure in either loader is re-raised as the serial path would raise it, with thread exhaustion degrading to serial.",
    sim=sim_parallel_load,
)

# ===============================================================================================================
# #839 Serve window scheduler: shortest request first, shared batches.
#
# One scorer thread on a fake clock; a batch of up to ROWS windows costs 1.0 + 0.1*rows. 'lock' is today's behaviour (one
# request at a time under the global lock). 'sjf' draws windows from all arrived requests, fewest remaining windows first,
# filling shared batches, with a starvation guard: a request that waited max_wait is served ahead of everything.

ROWS = 8


def _batch_cost(n):
    return 1.0 + 0.1 * n


def _windows(tag, n):
    return [tuple(random.Random(f"{tag}/{i}").randrange(50000) for _ in range(6)) for i in range(n)]


def _score_row(w):
    return zlib.crc32(repr(w).encode()) / 2**32  # per-row, like a masked transformer: no dependence on batch mates


def _score_batch(rows, poison):
    if poison and any(w in poison for w in rows):
        raise RuntimeError("bad window")
    return [_score_row(w) for w in rows]


def schedule(requests, policy, max_wait=None, poison=None):
    """requests: [{'id', 'arrive', 'windows'}]. Returns {'done': {id: t}, 'results': {id: [score]}, 'errors': {id}, 'batches': [[ids]]}."""
    st = {r["id"]: {"r": r, "next": 0, "res": [None] * len(r["windows"]), "done": None, "err": False} for r in requests}
    pending = sorted(st.values(), key=lambda s: (s["r"]["arrive"], s["r"]["id"]))
    arrived, batches, t = [], [], 0.0
    while pending or any(s["done"] is None for s in arrived):
        while pending and pending[0]["r"]["arrive"] <= t:
            arrived.append(pending.pop(0))
        active = [s for s in arrived if s["done"] is None]
        if not active:
            t = pending[0]["r"]["arrive"]
            continue
        if policy == "lock":
            order = [min(active, key=lambda s: (s["r"]["arrive"], s["r"]["id"]))]
        else:
            def key(s):
                expired = max_wait is not None and t - s["r"]["arrive"] >= max_wait
                left = len(s["r"]["windows"]) - s["next"]
                return (0, s["r"]["arrive"], s["r"]["id"]) if expired else (1, left, s["r"]["arrive"], s["r"]["id"])
            order = sorted(active, key=key)
        batch = []
        for s in order:
            n = len(s["r"]["windows"])
            while s["next"] < n and len(batch) < ROWS:
                batch.append((s, s["next"]))
                s["next"] += 1
            if len(batch) >= ROWS:
                break
        rows = [s["r"]["windows"][i] for s, i in batch]
        try:
            scores = _score_batch(rows, poison)
        except RuntimeError:  # isolate the bad row: retry rows one by one
            scores = []
            for (s, i), w in zip(batch, rows):
                try:
                    scores.append(_score_batch([w], poison)[0])
                except RuntimeError:
                    scores.append(None)
                    s["err"], s["next"] = True, len(s["r"]["windows"])  # purge the failed request's remaining windows
        t += _batch_cost(len(batch))
        batches.append([s["r"]["id"] for s, _ in batch])
        for (s, i), v in zip(batch, scores):
            if v is not None:
                s["res"][i] = v
        for s in {id(s): s for s, _ in batch}.values():
            if s["next"] >= len(s["r"]["windows"]):
                s["done"] = t
    return {"done": {k: s["done"] for k, s in st.items()}, "errors": {k for k, s in st.items() if s["err"]},
            "results": {k: s["res"] for k, s in st.items()}, "batches": batches}


def _pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, math.ceil(p * len(xs)) - 1)]


def sim_window_scheduler() -> dict:
    def oracle(req):
        return [_score_row(w) for w in req["windows"]]  # each request scored alone, no scheduler involved

    # (a) the idea's scenario: one 1 MB request (600 windows) and ten 7 KB ones (2 windows) arriving just after
    reqs = [{"id": "big", "arrive": 0.0, "windows": _windows("big", 600)}] + \
           [{"id": f"s{k}", "arrive": 0.5 + 0.05 * k, "windows": _windows(f"s{k}", 2)} for k in range(10)]
    lock, sjf = schedule(reqs, "lock"), schedule(reqs, "sjf", max_wait=60.0)
    lat = lambda res: [res["done"][f"s{k}"] - reqs[1 + k]["arrive"] for k in range(10)]  # noqa: E731
    for res in (lock, sjf):
        for r in reqs:
            assert res["results"][r["id"]] == oracle(r), f"request {r['id']} did not get its own results (cross-talk)"
    assert any(len(set(b)) > 1 for b in sjf["batches"]), "no batch actually mixed requests: the test would not catch cross-talk"
    p50l, p95l, p50s, p95s = _pct(lat(lock), .5), _pct(lat(lock), .95), _pct(lat(sjf), .5), _pct(lat(sjf), .95)
    assert p95s < 0.1 * p95l and p50s < 0.1 * p50l, f"small-request latency: lock p50/p95 {p50l:.1f}/{p95l:.1f}, sjf {p50s:.1f}/{p95s:.1f}"
    mk_l, mk_s = max(lock["done"].values()), max(sjf["done"].values())
    assert mk_s <= mk_l, f"shared batches must not cost throughput: makespan {mk_s:.1f} vs {mk_l:.1f}"
    assert schedule(reqs, "sjf", max_wait=60.0)["results"] == sjf["results"], "deterministic"

    # starvation: an overloaded stream of small requests; pure shortest-first never serves the long one until the stream ends
    stream = [{"id": f"x{k}", "arrive": 0.25 * (k + 1), "windows": _windows(f"x{k}", 2)} for k in range(480)]
    longr = {"id": "long", "arrive": 0.0, "windows": _windows("long", 120)}
    pure = schedule([longr] + stream, "sjf", max_wait=None)
    guard = schedule([longr] + stream, "sjf", max_wait=30.0)
    stream_end = stream[-1]["arrive"]
    assert pure["done"]["long"] > stream_end, "pure shortest-first starves the long request (this is what the guard fixes)"
    bound = 30.0 + _batch_cost(ROWS) * (1 + math.ceil(120 / ROWS))
    assert guard["done"]["long"] <= bound < stream_end, f"guarded long request finished at {guard['done']['long']:.1f}, bound {bound:.1f}"
    for res in (pure, guard):
        assert all(v is not None for v in res["done"].values()), "every request completes"
        for r in [longr] + stream[::37]:
            assert res["results"][r["id"]] == oracle(r)

    # (c) must NOT apply: a lone request, and equal-size requests (FIFO order), behave as the lock does
    one = [{"id": "a", "arrive": 0.0, "windows": _windows("a", 50)}]
    assert schedule(one, "sjf")["done"] == schedule(one, "lock")["done"]
    eq = [{"id": f"e{k}", "arrive": 0.1 * k, "windows": _windows(f"e{k}", 24)} for k in range(5)]
    done = schedule(eq, "sjf", max_wait=60.0)["done"]
    assert sorted(done, key=done.get) == [f"e{k}" for k in range(5)], "equal sizes finish in arrival order"

    # (d) failure handling: one poisoned window fails only its own request; batch mates still get correct results
    bad = _windows("victim", 3)
    mix = [{"id": "victim", "arrive": 0.0, "windows": bad}, {"id": "ok1", "arrive": 0.0, "windows": _windows("ok1", 4)},
           {"id": "ok2", "arrive": 0.0, "windows": _windows("ok2", 5)}]
    res = schedule(mix, "sjf", poison={bad[1]})
    assert res["errors"] == {"victim"}, res["errors"]
    for r in mix[1:]:
        assert res["results"][r["id"]] == oracle(r) and res["done"][r["id"]] is not None
    return {"small_p50_lock": round(p50l, 1), "small_p95_lock": round(p95l, 1), "small_p50_sjf": round(p50s, 1),
            "small_p95_sjf": round(p95s, 1), "makespan_lock": round(mk_l, 1), "makespan_sjf": round(mk_s, 1),
            "long_done_pure_sjf": round(pure["done"]["long"], 1), "long_done_guarded": round(guard["done"]["long"], 1)}


case(
    839,
    claim="A single scorer drawing windows from all queued requests shortest-first into shared batches gives every request exactly the results it would get alone, cuts small-request p50 and p95 latency by more than 10x against the global lock with no loss of throughput, a max-wait guard bounds the long request's completion under an overloaded stream where pure shortest-first starves it, and one poisoned window fails only its own request.",
    sim=sim_window_scheduler,
)

# ===============================================================================================================
# #842 Resolve model paths from an install-time manifest.
#
# Real code under test as the fallback and the oracle: ctxprune.compress._hub_file, with a fake `huggingface_hub` module
# (served by a meta-path finder that counts imports) over a fake HF cache directory made of real files and symlinks.

HF_REPO = "darioooooo0o/ctxprune-small"
HF_FILES = ("tokenizer.json", "onnx/model_quantized.onnx")


class FakeHub:
    def __init__(self, root):
        self.root = root
        self.imports = self.downloads = 0
        self.upstream = ("c1", {"tokenizer.json": b'{"model": 1}', "onnx/model_quantized.onnx": b"\x08\x07" * 5000})
        self.mdir = os.path.join(root, "models--" + HF_REPO.replace("/", "--"))

    def install(self, commit, files, set_ref=True):
        for name, data in files.items():
            blob = os.path.join(self.mdir, "blobs", hashlib.sha256(data).hexdigest())
            snap = os.path.join(self.mdir, "snapshots", commit, name)
            os.makedirs(os.path.dirname(blob), exist_ok=True)
            os.makedirs(os.path.dirname(snap), exist_ok=True)
            with open(blob, "wb") as f:
                f.write(data)
            if os.path.lexists(snap):
                os.remove(snap)
            os.symlink(os.path.relpath(blob, os.path.dirname(snap)), snap)
        if set_ref:
            os.makedirs(os.path.join(self.mdir, "refs"), exist_ok=True)
            with open(os.path.join(self.mdir, "refs", "main"), "w") as f:
                f.write(commit)

    @property
    def ref_path(self):
        return os.path.join(self.mdir, "refs", "main")

    def hf_hub_download(self, repo, filename, local_files_only=False):
        try:
            commit = open(self.ref_path).read().strip()
            p = os.path.join(self.mdir, "snapshots", commit, filename)
            if os.path.exists(p):
                return p
        except OSError:
            pass
        if local_files_only:
            raise FileNotFoundError("not in local cache")
        self.downloads += 1
        self.install(*self.upstream)
        return os.path.join(self.mdir, "snapshots", self.upstream[0], filename)


class _HubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, hub):
        self.hub = hub

    def find_spec(self, name, path=None, target=None):
        return importlib.util.spec_from_loader(name, self) if name == "huggingface_hub" else None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        self.hub.imports += 1  # the import cost the idea wants to avoid
        module.hf_hub_download = self.hub.hf_hub_download


def write_manifest(path, model, hub, files=HF_FILES):
    """Install-time step: resolve through the hub once, record absolute paths, sizes, mtimes and the ref they came from."""
    from ctxprune.compress import _hub_file

    ent = {}
    for f in files:
        p = _hub_file(model, f)
        st = os.stat(p)
        ent[f] = {"path": p, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    m = {"v": 1, "model": model, "cache_root": hub.root, "ref_path": hub.ref_path,
         "commit": open(hub.ref_path).read().strip(), "files": ent}
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(m, fh)
    os.replace(tmp, path)  # atomic: a reader sees the old or the new manifest, never half of one


def resolve_files(model, filenames, manifest_path, cache_root, fallback):
    """Paths of the model files, without importing the hub when the manifest is still true for ALL of them. Any doubt means
    the old path, and the old path is run twice so that a download triggered by one file cannot leave the files on two
    different revisions (the second pass is answered from the cache the first one filled)."""
    if not os.path.isdir(model):
        try:
            with open(manifest_path) as fh:
                m = json.load(fh)
            if m["v"] != 1 or m["model"] != model or m["cache_root"] != cache_root:
                raise ValueError("manifest is for another model or cache")
            if open(m["ref_path"]).read().strip() != m["commit"]:
                raise ValueError("refs/main moved: a newer snapshot is current")
            out = []
            for f in filenames:
                e = m["files"][f]
                st = os.stat(e["path"])  # follows the symlink; raises if deleted or dangling
                if st.st_size != e["size"] or st.st_mtime_ns != e["mtime_ns"]:
                    raise ValueError("file changed")
                out.append(e["path"])
            return out
        except Exception:  # noqa: BLE001  missing, corrupt, stale: all mean "ask the hub as before"
            pass
    for f in filenames:
        fallback(model, f)
    return [fallback(model, f) for f in filenames]


def sim_manifest() -> dict:
    ctx = _ctx()
    real_hub_file = ctx.compress._hub_file
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        hub = FakeHub(os.path.join(tmp, "hub"))
        manifest = os.path.join(tmp, "paths.json")
        finder = _HubFinder(hub)
        sys.meta_path.insert(0, finder)
        saved = sys.modules.pop("huggingface_hub", None)

        def cold(fn):
            """A fresh process: the hub module is not imported yet."""
            sys.modules.pop("huggingface_hub", None)
            hub.imports = 0
            return fn()

        def via_manifest(model=HF_REPO, root=None):
            return resolve_files(model, HF_FILES, manifest, root or hub.root, real_hub_file)

        def via_hub():  # what the current code does, one file after the other
            return [real_hub_file(HF_REPO, f) for f in HF_FILES]

        try:
            hub.install(*hub.upstream)
            cold(via_hub)
            write_manifest(manifest, HF_REPO, hub)

            # (a) valid manifest: same paths as the hub gives, zero hub imports over 10 cold starts
            for _ in range(10):
                got = cold(via_manifest)
                assert hub.imports == 0, "a valid manifest must not import the hub"
            want = cold(via_hub)
            assert got == want and hub.imports == 1, "same paths as _hub_file, which does pay the import"
            assert [open(p, "rb").read() for p in got] == [open(p, "rb").read() for p in want], "reader view: same file bytes"
            out["hub_imports_10_cold_starts_with_manifest"], out["without"] = 0, 10

            def falls_back(label, expect_download=0, **kw):
                hub.downloads = 0
                got = cold(lambda: via_manifest(**kw))
                assert hub.imports == 1, f"{label}: must fall back to the hub (imports={hub.imports})"
                assert hub.downloads == expect_download, f"{label}: downloads={hub.downloads}"
                assert all(os.path.exists(p) for p in got), f"{label}: returned a path that does not exist"
                assert got == cold(via_hub), f"{label}: fallback must equal what the hub resolves"
                return got

            # (c)/(d) stale or broken manifests fall back and agree with the hub
            old = cold(via_manifest)
            hub.install("c2", {"tokenizer.json": b'{"model": 2}', "onnx/model_quantized.onnx": b"\x09" * 9000})  # repo updated, ref moved
            new = falls_back("ref moved")
            assert new != old and all("/c2/" in p for p in new), "stale manifest must not pin the old revision"
            write_manifest(manifest, HF_REPO, hub)
            assert cold(via_manifest) == new and hub.imports == 0, "manifest rewritten: fast again"

            keep = open(os.path.realpath(new[1]), "rb").read()
            os.remove(new[1])  # model file deleted (dangling manifest entry); the tokenizer entry is still fine
            got = falls_back("file deleted", expect_download=1)
            assert len({re.search(r"/snapshots/([^/]+)/", p).group(1) for p in got}) == 1, \
                "all files must come from ONE revision after a fallback download"
            write_manifest(manifest, HF_REPO, hub)

            blob = os.path.realpath(cold(via_manifest)[1])
            good = open(blob, "rb").read()
            with open(blob, "wb") as fh:  # truncated partial download: size differs
                fh.write(good[:100])
            falls_back("size changed")
            with open(blob, "wb") as fh:
                fh.write(good)
            write_manifest(manifest, HF_REPO, hub)
            with open(blob, "r+b") as fh:  # same size, different bytes: only mtime can tell
                fh.write(b"\x00")
            os.utime(blob, ns=(10**18, 10**18))
            falls_back("same size, new mtime")
            with open(blob, "wb") as fh:
                fh.write(good)
            write_manifest(manifest, HF_REPO, hub)

            for label, content in (("missing manifest", None), ("empty", ""), ("truncated json", '{"v": 1, "mod'),
                                   ("wrong schema", "[1, 2]"), ("wrong version", json.dumps({"v": 2}))):
                saved_text = open(manifest).read()
                if content is None:
                    os.remove(manifest)
                else:
                    open(manifest, "w").write(content)
                falls_back(label)
                open(manifest, "w").write(saved_text)
            assert cold(via_manifest) and hub.imports == 0, "restored manifest is trusted again"

            falls_back("other cache root (HF_HOME changed)", root=os.path.join(tmp, "other-cache"))
            falls_back("manifest written for another model", model="someone/else")

            local = os.path.join(tmp, "checkout")  # a local model directory never consults the manifest or the hub
            os.makedirs(local)
            assert cold(lambda: resolve_files(local, HF_FILES, manifest, hub.root, real_hub_file)) == [os.path.join(local, f) for f in HF_FILES]
            assert hub.imports == 0
        finally:
            sys.meta_path.remove(finder)
            sys.modules.pop("huggingface_hub", None)
            if saved is not None:
                sys.modules["huggingface_hub"] = saved
    return out


case(
    842,
    claim="With a valid install-time manifest the model files resolve to the same paths and bytes as compress._hub_file without importing huggingface_hub (0 imports over 10 cold starts versus 10), and a moved ref, deleted, truncated or same-size-rewritten file, missing, empty, corrupt or wrong-version manifest, or a different cache root all fall back to the hub and return exactly what it resolves.",
    sim=sim_manifest,
)

# ===============================================================================================================
# #870 Prompt-keyword raw mode: 'verbatim' in the newest prompt bypasses the hook for that turn.
#
# Real code: hook.run (whose result None means "Claude keeps the original output"). New code: newest_prompt() (bounded tail read
# of the transcript JSONL, skipping tool results, meta and sidechain entries) and wants_raw() (the keyword matcher).
# False-positive cost = a turn whose large outputs are not compressed; false-negative cost = one compressed output, whose
# original hook.run saves and names, so the user or Claude can still read it. Both are measured on a labelled prompt corpus.

_SYSREM = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_QUOTED = re.compile(r"```.*?```|`[^`\n]*`|\"[^\"\n]*\"|“[^”\n]*”|‘[^’\n]*’|(?<![A-Za-z0-9])'[^'\n]*'(?![A-Za-z0-9])", re.S)
_KEYWORD = re.compile(r"(?<![\w/\\\-])(?:verbatim|exact[ -]outputs?|full[ -]logs?)(?![\w/\\]|-\w)", re.I)
_CLAUSE = re.compile(r"[.;:!?\n,()]+")
_NEGATORS = {"not", "no", "never", "without", "avoid", "skip", "disable", "off", "ignore", "forget", "stop", "dont",
             "cannot", "cant"}
_SCOPE_BREAK = {"and", "but", "then", "instead", "so", "just", "also", "plus"}
_LONG_PROMPT = 2000


def wants_raw(prompt: str) -> bool:
    """True when the prompt asks for unprocessed output. Quoted or backticked words are mentions, not requests; a negator within
    four words before the keyword (without a conjunction in between) cancels it; word boundaries keep 'full logging',
    'nonverbatim' and paths like src/verbatim/x.py out. Prompts over 2000 characters are scanned only in their first line (the rest is pasted data)."""
    try:
        text = re.sub(r"(?<=\w)[’']", "'", prompt)
        if len(text) > _LONG_PROMPT:
            text = text.split("\n", 1)[0][:300]
        text = _QUOTED.sub(" ", text)
        for clause in _CLAUSE.split(text):
            for m in _KEYWORD.finditer(clause):
                before = re.findall(r"[a-z']+", clause[:m.start()].lower())[-4:]
                scope = []
                for w in reversed(before):
                    if w in _SCOPE_BREAK:
                        break
                    scope.append(w)
                if not any(w in _NEGATORS or w.endswith("n't") for w in scope):
                    return True
        return False
    except Exception:  # noqa: BLE001
        return False  # unknown intent: keep the default (compress)


_NOT_A_PROMPT = object()


def _prompt_of(line: bytes):
    if b'"user"' not in line:
        return _NOT_A_PROMPT
    try:
        e = json.loads(line)
        if not isinstance(e, dict) or e.get("type") != "user" or e.get("isMeta") or e.get("isSidechain"):
            return _NOT_A_PROMPT
        c = (e.get("message") or {}).get("content")
        if isinstance(c, list):
            c = "\n".join(b["text"] for b in c if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
        if not isinstance(c, str):
            return _NOT_A_PROMPT
        c = _SYSREM.sub("", c).strip()
        return c or _NOT_A_PROMPT
    except Exception:  # noqa: BLE001
        return _NOT_A_PROMPT


def newest_prompt(path: str, start: int = 65536, max_cap: int = 1 << 20):
    """(prompt or None, bytes read). Reads only a tail window of the file, growing it 4x until a prompt is found or max_cap."""
    size = os.path.getsize(path)
    cap, read_total = start, 0
    while True:
        n = min(cap, size)
        with open(path, "rb") as f:
            f.seek(size - n)
            data = f.read(n)
        read_total += n
        lines = data.split(b"\n")
        if n < size:
            lines = lines[1:]  # the first line is probably cut in half
        for line in reversed(lines):
            p = _prompt_of(line)
            if p is not _NOT_A_PROMPT:
                return p, read_total
        if n >= size or cap >= max_cap:
            return None, read_total
        cap *= 4


def run_with_bypass(hook, event, rate, min_chars):
    tp = event.get("transcript_path")
    if isinstance(tp, str) and tp:
        try:
            prompt, _ = newest_prompt(tp)
            if prompt is not None and wants_raw(prompt):
                return None
        except Exception:  # noqa: BLE001  unreadable transcript: behave as without the feature
            pass
    return hook.run(event, rate, min_chars)


def _naive(prompt):  # the obvious implementation, for the false-positive comparison
    p = prompt.lower()
    return "verbatim" in p or "exact output" in p or "full log" in p


_PASTE = "".join(f"2026-11-04T10:{i % 60:02d}:11Z INFO worker-{i} copied the payload verbatim to the buffer\n" for i in range(60))

# (prompt, user really wants raw output)
PROMPTS_TRUE = [
    "Run the tests and show me the exact output.", "give me the full log of the failing job", "Print the verbatim output of `make` please",
    "I need the verbatim error message, not a summary.", "VERBATIM please", "show full-log", "Don't summarize, I want the full log.",
    "No need to explain. Show the exact output.", "paste the exact outputs of both commands",
    "Please run it again and give me the output verbatim.", "do not summarize and show verbatim output",
    "Why did it fail? I want the verbatim stderr.", "Show me the exact output of the failing step.\n\n" + _PASTE,
]
PROMPTS_FALSE = [
    "do not use verbatim mode", "Don't give me the full log, just the failing test name.", "avoid the exact output, summarize instead",
    "no need for the full log", 'the docs say "verbatim" but I think that is wrong', "rename the `verbatim` option to raw",
    "see src/verbatim/parser.py for the bug", "use the --verbatim flag in the next run", "the full logging config is wrong",
    "nonverbatim handling is broken and so are verbatims", "check the full logic of this function", "there is an exact outputs_dir variable",
    "Summarize the build", "turn off verbatim mode", "I'd rather not see the exact output", "I can’t stand the full log, summarize it",
    "Here is the CI log, what failed?\n" + _PASTE + "\nIs this a flaky test?", "Here is the CI log:\n" + _PASTE,
    "```\ncopy verbatim\n```\nwhy does this crash?",
    "Summarize everything, don't give me the verbatim log.",
]
# known wrong answers, pinned so a change in behaviour is noticed: (prompt, truth, what the matcher says)
PROMPTS_RESIDUAL = [
    ("the helper copies the bytes verbatim into the buffer, is that safe?", False, True),   # describes code, not a request: false positive
    ("Why doesn't it show the full log?", True, False),                                      # negated question that wants it: false negative
    ('show me the "exact output"', True, False),                                             # quotes used as emphasis: false negative
    ("Here is the CI log:\n" + _PASTE + "\nPlease show the verbatim failing line.", True, False),  # request after a long paste: false negative
]


def _tx(*entries):
    return "".join(json.dumps(e) + "\n" for e in entries)


def _u(content, **kw):
    return {"type": "user", "message": {"role": "user", "content": content}, **kw}


def _tool(i, cmd="make test"):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": cmd}}]}}


def _result(i, text):
    return _u([{"type": "tool_result", "tool_use_id": f"t{i}", "content": text}])


def sim_verbatim_bypass() -> dict:
    ctx = _ctx()
    hook = ctx.hook
    big = ("the build step finished with a warning about the cache directory being slow " * 120).strip()

    # --- matcher on the corpus
    for p in PROMPTS_TRUE:
        assert wants_raw(p), f"missed a clear request: {p[:60]!r}"
    for p in PROMPTS_FALSE:
        assert not wants_raw(p), f"false positive: {p[:60]!r}"
    for p, truth, said in PROMPTS_RESIDUAL:
        assert wants_raw(p) == said != truth, f"residual case changed behaviour: {p!r}"
    naive_fp = sum(_naive(p) for p in PROMPTS_FALSE)
    smart_fp = sum(wants_raw(p) for p in PROMPTS_FALSE) + sum(wants_raw(p) for p, t, _ in PROMPTS_RESIDUAL if not t)
    assert naive_fp >= 12 and smart_fp <= 1, (naive_fp, smart_fp)
    assert not wants_raw("") and not wants_raw("\x00\ud800 mojibake") and wants_raw("\ud800 verbatim please"), "empty and odd text"

    # --- whole-hook behaviour per turn, with the real hook.run
    fc = _fake_compressor("870")
    saved_compress = hook._compress
    hook._compress = lambda texts, rate: [fc.compress(t, rate=rate, force_protected=True)["text"] for t in texts]
    stats = {}
    try:
        with tempfile.TemporaryDirectory() as tmp, _env(XDG_CACHE_HOME=os.path.join(tmp, "cache")):
            tp = os.path.join(tmp, "t.jsonl")
            ev = lambda: {**_event(big, "make test"), "transcript_path": tp}  # noqa: E731
            turns = [
                ("run the build", True),
                ("show me the verbatim output of the test run", False),
                ("thanks, now check the lint", True),                      # the keyword was one prompt ago: new turn, compress again
                ([{"type": "text", "text": "do not use verbatim mode, summarize"}], True),
            ]
            log, saved_chars, bypassed_chars = [], 0, 0
            for k, (prompt, expect_compress) in enumerate(turns):
                log += [_u(prompt), _tool(k)]
                if k == 1:  # noise after the keyword prompt that must not be mistaken for a newer prompt
                    log += [_result(k, "full log verbatim exact output"), _u("full log", isMeta=True),
                            _u("verbatim please", isSidechain=True), _u("<system-reminder>exact output</system-reminder>")]
                else:
                    log += [_result(k, "tool printed: show me the verbatim output")]  # tool output mentioning the word
                open(tp, "w").write(_tx(*log))
                r = run_with_bypass(hook, ev(), 0.5, 6000)
                assert (r is not None) == expect_compress, f"turn {k} ({prompt!r}): compress={r is not None}"
                if r is not None:
                    saved_chars += len(big) - len(r["hookSpecificOutput"]["updatedToolOutput"]["stdout"])
                else:
                    bypassed_chars += len(big)
                    assert hook.run(ev(), 0.5, 6000) is not None, "without the feature this turn would have been compressed"
            assert saved_chars > 0.4 * 3 * len(big) and bypassed_chars == len(big)
            stats.update(chars_saved_on_3_normal_turns=saved_chars, chars_passed_through_on_keyword_turn=bypassed_chars)

            # reader view: a false negative still leaves the full original, named in the note
            open(tp, "w").write(_tx(_u("Why doesn't it show the full log?"), _tool(9)))
            r = run_with_bypass(hook, ev(), 0.5, 6000)
            path = re.search(r"is at (\S+) \.", r["hookSpecificOutput"]["additionalContext"]).group(1)
            assert open(path, encoding="utf-8").read() == big, "the original must be recoverable after a false negative"

            # false-positive cost, explicit: a FP turn forgoes compression of its large outputs
            per_turn_chars, rate = 3 * 12000, 0.5
            stats["fp_cost_chars_per_false_positive_turn"] = int(per_turn_chars * (1 - rate))
            stats["false_positives_naive_vs_matcher"] = f"{naive_fp} vs {smart_fp} of {len(PROMPTS_FALSE) + sum(not t for _, t, _ in PROMPTS_RESIDUAL)}"

            # --- transcript tail read over 1,000 events
            rng = random.Random(870)
            events = []
            for t in range(110):  # 110 turns x (1 prompt + 4 tool calls x 2 entries) = 990 events, then a heavy final turn
                events.append(_u(f"step {t}: look at the logs"))
                for j in range(4):
                    events += [_tool(t * 4 + j), _result(t * 4 + j, "x" * rng.randrange(500, 9000))]
            events.append(_u("show me the exact output of the last build"))
            for j in range(8):  # 8 results of 30 KB after the prompt: the 64 KB first window is not enough
                events += [_tool(1000 + j), _result(1000 + j, "y" * 30000)]
            open(tp, "w").write(_tx(*events))
            size = os.path.getsize(tp)
            full = None
            for line in open(tp, "rb"):  # independent oracle: parse everything from the top, keep the last real prompt
                e = json.loads(line)
                if e["type"] == "user" and isinstance(e["message"]["content"], str):
                    full = e["message"]["content"]
            prompt, n_read = newest_prompt(tp)
            assert prompt == full == "show me the exact output of the last build"
            assert 65536 < n_read < size / 4, f"tail read must grow its window but not scan the file: read {n_read} of {size} bytes"
            stats.update(transcript_events=len(events), transcript_bytes=size, bytes_read=n_read)
            # a prompt buried under more than max_cap of tool results is not found: default behaviour, not an error
            open(tp, "w").write(_tx(_u("show the verbatim output"), _tool(1), _result(1, "z" * (3 << 20))))
            prompt, n_read = newest_prompt(tp)
            assert prompt is None and n_read <= (1 << 20) * 4 // 3 + 65536, n_read  # windows 64K, 256K, 1M: bounded, not the 3 MB file
            assert run_with_bypass(hook, ev(), 0.5, 6000) is not None, "unknown intent: compress as usual"

            # --- failure handling: never raises, never bypasses by accident
            for label, blob in (("garbage", b"\xff\xfe\x00not json\n{\n"), ("empty", b""), ("no prompts", _tx(_tool(1)).encode()),
                                ("prompt line cut in half", _tx(_u("verbatim please")).encode()[:-9])):
                open(tp, "wb").write(blob)
                assert run_with_bypass(hook, ev(), 0.5, 6000) is not None, label
            for tpath in (os.path.join(tmp, "missing.jsonl"), tmp, None, 7):
                e2 = {**_event(big), "transcript_path": tpath}
                assert run_with_bypass(hook, e2, 0.5, 6000) is not None
            assert run_with_bypass(hook, _event(big), 0.5, 6000) is not None, "no transcript_path at all"
    finally:
        hook._compress = saved_compress
    return stats


case(
    870,
    claim="Only the newest real user prompt decides: 'verbatim', 'exact output' or 'full log' as a request (not quoted, negated, a substring such as 'full logging', or in a path) makes hook.run's compression bypass for that turn only, tool results, meta and sidechain entries are ignored, the transcript is read from the tail in bounded windows, and a missing or unreadable transcript keeps the default compress behaviour; residual false positives and negatives are pinned.",
    sim=sim_verbatim_bypass,
)

# ===============================================================================================================
# #1195 Memory-mapped external-data weights shared across hook processes.
#
# Stand-in for an ONNX model: weights are float32 matrices; the 'single-file' build reads them into private memory, the
# 'external-data' build keeps them in a separate raw file that is mmap'ed read-only (what ORT does for external initializers).
# Real separate processes map the same file; /proc/<pid>/smaps_rollup gives PSS (shared pages split across the mappers).

LAYERS, DIM = 4, 512
WEIGHT_BYTES = LAYERS * DIM * DIM * 4  # 4 MiB


def _make_weights():
    raw = hashlib.shake_256(b"ctxprune-weights").digest(LAYERS * DIM * DIM)
    return array.array("f", ((b - 128) / 128 for b in raw)).tobytes()


class StandInSession:
    """Same arithmetic whatever the storage: logits depend on the weight bytes only."""

    def __init__(self, view):
        self.w = memoryview(view).cast("B").cast("f")

    def logits(self, tokens):
        out = []
        for t in tokens:
            row = []
            for k in range(LAYERS):
                base = (k * DIM + (t * 31 + k) % DIM) * DIM
                row.append(math.fsum(self.w[base:base + DIM]))
            out.append(tuple(row))
        return out


def build_single(path, weights):
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(weights)) + weights)


def build_external(graph_path, data_path, weights):
    with open(data_path, "wb") as f:
        f.write(weights)
    head = hashlib.sha256(weights[:4096]).hexdigest(), hashlib.sha256(weights[-4096:]).hexdigest()
    with open(graph_path, "w") as f:
        json.dump({"location": os.path.basename(data_path), "size": len(weights), "head_tail_sha256": head}, f)


def open_session(single_path, graph_path):
    """Prefer the mmap'ed external-data model; any doubt means the single-file model. Returns (session, kind, keepalive)."""
    try:
        g = json.load(open(graph_path))
        data_path = os.path.join(os.path.dirname(graph_path), g["location"])
        if os.path.getsize(data_path) != g["size"]:
            raise ValueError("external data has the wrong size")
        f = open(data_path, "rb")
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)  # read-only: the shared cache can never be written through it
        if (hashlib.sha256(mm[:4096]).hexdigest(), hashlib.sha256(mm[-4096:]).hexdigest()) != tuple(g["head_tail_sha256"]):
            mm.close()
            raise ValueError("external data does not match the graph")
        return StandInSession(mm), "mmap", (f, mm)
    except Exception:  # noqa: BLE001  missing, truncated, empty (mmap raises ValueError), no address space, wrong file
        data = open(single_path, "rb").read()
        return StandInSession(memoryview(data)[8:]), "read", data


_CHILD = r"""
import sys, mmap, json
mode, path = sys.argv[1], sys.argv[2]
def smaps():
    d = {}
    for line in open("/proc/self/smaps_rollup"):
        k, _, v = line.partition(":")
        v = v.split()
        if v and v[-1] == "kB":
            d[k] = int(v[0])
    return d
base = smaps()
if mode == "mmap":
    f = open(path, "rb")
    hold = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
else:
    hold = open(path, "rb").read()
created = smaps()
t = 0
for i in range(0, len(hold), 4096):
    t += hold[i]
print("ready", flush=True)
sys.stdin.readline()
print(json.dumps({"base": base, "created": created, "now": smaps()}), flush=True)
sys.stdin.readline()
"""


def _measure(mode, path, n=3):
    ps = [subprocess.Popen([sys.executable, "-I", "-c", _CHILD, mode, path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
          for _ in range(n)]
    try:
        for p in ps:
            assert p.stdout.readline().strip() == "ready"
        for p in ps:
            p.stdin.write("go\n")
            p.stdin.flush()
        res = [json.loads(p.stdout.readline()) for p in ps]  # every child still holds its mapping while the others measure
        for p in ps:
            p.stdin.write("bye\n")
            p.stdin.flush()
    finally:
        for p in ps:
            p.stdin.close()
            p.wait(timeout=10)
    return res


def sim_mmap_weights() -> dict:
    out = {}
    weights = _make_weights()
    with tempfile.TemporaryDirectory() as d:
        single, graph, data = (os.path.join(d, n) for n in ("model.bin", "model.graph.json", "model.weights"))
        build_single(single, weights)
        build_external(graph, data, weights)

        # identical bytes and identical logits
        s_read, kind_r, keep_r = StandInSession(memoryview(open(single, "rb").read())[8:]), "read", None
        s_map, kind_m, keep_m = open_session(single, graph)
        assert kind_m == "mmap"
        assert hashlib.sha256(memoryview(keep_m[1])).digest() == hashlib.sha256(weights).digest(), "mapped bytes differ from the file"
        for toks in ([1, 2, 3], [511, 0, 17, 99], list(range(0, 400, 37))):
            assert s_map.logits(toks) == s_read.logits(toks), "logits must be bit-identical between the two storage forms"
        try:
            keep_m[1][0:1] = b"x"
        except TypeError:
            pass
        else:
            raise AssertionError("the mapping must be read-only")
        s_map.w.release()
        keep_m[1].close()
        keep_m[0].close()

        # shared pages: three real processes, summed PSS
        if os.path.exists("/proc/self/smaps_rollup"):
            size_kb = WEIGHT_BYTES // 1024
            m = _measure("mmap", data)
            r = _measure("read", data)
            pss_m = sum(x["now"]["Pss"] - x["base"]["Pss"] for x in m)
            pss_r = sum(x["now"]["Pss"] - x["base"]["Pss"] for x in r)
            rss_create_m = max(x["created"]["Rss"] - x["base"]["Rss"] for x in m)
            rss_create_r = min(x["created"]["Rss"] - x["base"]["Rss"] for x in r)
            assert pss_r >= 2.7 * size_kb, f"measurement sanity: 3 private copies should be ~{3 * size_kb} kB, got {pss_r}"
            assert pss_m <= 1.35 * size_kb, f"3 mappers of one file should share ONE copy (~{size_kb} kB), summed PSS {pss_m}"
            assert rss_create_m < 0.25 * size_kb <= 0.9 * size_kb <= rss_create_r, "mapping is lazy; read() copies everything up front"
            out.update(pss_sum_3_procs_mmap_kb=pss_m, pss_sum_3_procs_read_kb=pss_r, weights_kb=size_kb,
                       rss_at_session_creation_mmap_kb=rss_create_m, rss_at_session_creation_read_kb=rss_create_r)
        else:
            out["pss_checked"] = False  # no /proc: only the byte and logit checks ran

        # (c)/(d) must NOT apply / fail open: each of these must yield the single-file model with equal logits
        want = s_read.logits([5, 6, 7])
        os.rename(data, data + ".gone")
        s, kind, _ = open_session(single, graph)
        assert kind == "read" and s.logits([5, 6, 7]) == want, "missing external file"
        os.rename(data + ".gone", data)
        with open(data, "wb") as f:
            f.write(weights[: WEIGHT_BYTES // 2])
        s, kind, _ = open_session(single, graph)
        assert kind == "read" and s.logits([5, 6, 7]) == want, "truncated external file"
        with open(data, "wb") as f:
            f.write(b"")
        s, kind, _ = open_session(single, graph)
        assert kind == "read" and s.logits([5, 6, 7]) == want, "empty file: mmap raises ValueError"
        with open(data, "wb") as f:
            f.write(bytes(reversed(weights)))  # same size, wrong content
        s, kind, _ = open_session(single, graph)
        assert kind == "read" and s.logits([5, 6, 7]) == want, "same size, different bytes"
        with open(data, "wb") as f:
            f.write(weights)
        real_mmap = mmap.mmap

        def no_address_space(*a, **k):
            raise OSError(12, "Cannot allocate memory")

        mmap.mmap = no_address_space
        try:
            s, kind, _ = open_session(single, graph)
        finally:
            mmap.mmap = real_mmap
        assert kind == "read" and s.logits([5, 6, 7]) == want, "mmap failing at runtime"
        s, kind, keep = open_session(single, graph)
        assert kind == "mmap", "restored files are used again"
        s.w.release()
        keep[1].close()
        keep[0].close()
    return out


case(
    1195,
    claim="A stand-in model with its weights in a separate raw file mapped read-only gives bit-identical logits to the single-file build, three real processes mapping it have a summed PSS of about one copy instead of three, creating the session copies nothing up front, and a missing, truncated, empty, same-size-but-different or unmappable file falls back to the single-file model with equal logits.",
    sim=sim_mmap_weights,
)

# ===============================================================================================================
# #1198 Cancel abandoned serve work when the hook client disconnects.
#
# The disconnect probe is real (a socketpair, recv(MSG_PEEK|MSG_DONTWAIT)); the clock and the scorer are fake. serve scores one
# request at a time under a global lock, in batches of 16 windows at 0.5 s a batch; the client socket is polled between batches.

BATCH16, BATCH_S = 16, 0.5


def peer_gone(sock) -> bool:
    """True when the client has closed its end. Unread data (a pipelined next request) means it is still there."""
    flag = getattr(socket, "MSG_DONTWAIT", None)
    if flag is None:
        return False  # cannot poll without blocking on this platform: never cancel
    try:
        return sock.recv(1, socket.MSG_PEEK | flag) == b""
    except (BlockingIOError, InterruptedError):
        return False
    except OSError:
        return True  # reset by peer


class Conn:
    def __init__(self, close_at=None):
        self.client, self.server = socket.socketpair()
        self.close_at = close_at

    def tick(self, t):
        if self.close_at is not None and t >= self.close_at and self.client.fileno() != -1:
            self.client.close()

    def done(self):
        for s in (self.client, self.server):
            s.close()


def serve_all(reqs, cancel, poll=peer_gone):
    """One request at a time, FIFO. Returns per-request done time and result, rows scored, and the shared result cache."""
    t, rows_scored, cache, out = 0.0, 0, {}, {}
    for r in sorted(reqs, key=lambda r: (r["arrive"], r["id"])):
        t = max(t, r["arrive"])
        conn = Conn(r.get("close_at"))
        try:
            if r["key"] in cache:
                out[r["id"]] = {"done": t, "result": cache[r["key"]], "cancelled": False}
                continue
            wins, res, cancelled = _windows(r["key"], r["n"]), [], False
            for i in range(0, len(wins), BATCH16):
                conn.tick(t)
                if cancel:
                    try:
                        gone = poll(conn.server)
                    except Exception:  # noqa: BLE001  a broken probe must not break serving: assume the client is there
                        gone = False
                    if gone:
                        cancelled = True
                        break
                res += [_score_row(w) for w in wins[i:i + BATCH16]]
                rows_scored += len(wins[i:i + BATCH16])
                t += BATCH_S
            conn.tick(t)
            if not cancelled:
                cache[r["key"]] = res  # only complete results are ever cached
            out[r["id"]] = {"done": t, "result": None if cancelled else res, "cancelled": cancelled}
        finally:
            conn.done()
    return out, rows_scored, cache


def sim_cancel_on_disconnect() -> dict:
    full = lambda key, n: [_score_row(w) for w in _windows(key, n)]  # noqa: E731  oracle: scored alone, no server
    big = {"id": "big", "arrive": 0.0, "key": "big2mb", "n": 1000, "close_at": 1.0}
    small = {"id": "small", "arrive": 2.0, "key": "small", "n": 2}

    # (a) the idea's scenario: a 2 MB request abandoned after 1 s, then a small one
    base, rows_base, _ = serve_all([big, small], cancel=False)
    canc, rows_canc, cache = serve_all([big, small], cancel=True)
    lat_base, lat_canc = base["small"]["done"] - 2.0, canc["small"]["done"] - 2.0
    assert lat_base > 25 and lat_canc <= 1.0, f"small-request latency {lat_base:.1f}s without cancel, {lat_canc:.1f}s with"
    assert canc["big"]["cancelled"] and canc["big"]["result"] is None
    assert rows_canc <= 3 * BATCH16 + 2 and rows_base == 1002, f"rows scored {rows_canc} vs {rows_base}"
    assert canc["small"]["result"] == base["small"]["result"] == full("small", 2), "the small request's result is unaffected"
    assert "big2mb" not in cache, "a cancelled request's partial result must never be cached"

    # reader view: the same request retried later by a live client is computed in full and correct
    retry = {"id": "retry", "arrive": 5.0, "key": "big2mb", "n": 1000}
    out, _, cache = serve_all([big, small, retry], cancel=True)
    assert out["retry"]["result"] == full("big2mb", 1000) and len(out["retry"]["result"]) == 1000 and "big2mb" in cache

    # a request that queued behind the big one and whose client gave up while waiting costs zero work
    ghost = {"id": "ghost", "arrive": 0.5, "key": "ghost", "n": 400, "close_at": 3.0}
    out, rows, _ = serve_all([{**big, "close_at": None, "n": 160}, ghost], cancel=True)
    assert out["ghost"]["cancelled"] and rows == 160

    # (c) must NOT apply
    out, rows, cache = serve_all([{**big, "close_at": None}], cancel=True)  # client still connected
    assert out["big"]["result"] == full("big2mb", 1000) and rows == 1000 and not out["big"]["cancelled"]
    late = {**big, "n": 32, "close_at": 1.0}  # client closes exactly when the work is done: nothing to drop, result is cached
    out, rows, cache = serve_all([late], cancel=True)
    assert out["big"]["result"] == full("big2mb", 32) and "big2mb" in cache

    # (d) failure handling: a probe that raises must not cancel or break the request
    def broken(sock):
        raise RuntimeError("probe failed")

    out, rows, _ = serve_all([big], cancel=True, poll=broken)
    assert out["big"]["result"] == full("big2mb", 1000) and rows == 1000

    # the probe itself, on real sockets
    a, b = socket.socketpair()
    assert not peer_gone(b), "idle connected client"
    a.send(b"POST /compress")
    assert not peer_gone(b) and b.recv(1) == b"P", "pipelined bytes mean the client is alive, and peeking must not consume them"
    a.close()
    assert peer_gone(b) is False, "unread data still queued: not gone until drained"
    b.recv(100)
    assert peer_gone(b), "closed client"
    b.close()
    a, b = socket.socketpair()
    a.shutdown(socket.SHUT_WR)
    half_closed_counts_as_gone = peer_gone(b)  # pinned limitation: a client that half-closes while waiting looks disconnected
    assert half_closed_counts_as_gone is True, "documented limitation: write-shutdown is indistinguishable from close"
    a.close()
    b.close()
    return {"small_latency_s_without_cancel": lat_base, "small_latency_s_with_cancel": lat_canc, "rows_scored_without_cancel": rows_base,
            "rows_scored_with_cancel": rows_canc, "half_close_treated_as_disconnect": half_closed_counts_as_gone}


case(
    1198,
    claim="Polling the client socket between batches drops the remaining windows of an abandoned 2 MB request (small-request latency 30 s to 0.5 s, 1002 rows scored to 34), a cancelled request's partial result is never cached and a retry is computed in full, a queued request whose client left costs nothing, and a connected client, a late close or a failing probe change nothing.",
    sim=sim_cancel_on_disconnect,
)

# ===============================================================================================================
# #1199 Derive ORT threads and a memory guard from cgroup limits.
#
# Parsing is tested on sample file contents behind a fake filesystem (cgroup v2, v1 and the hybrid layout this container has),
# including 'max', -1 and the v1 page-aligned LONG_MAX 'unlimited' sentinel, nested cgroups (the tightest ancestor wins) and
# garbage. The real files are read once to show the reader never raises.

import posixpath  # noqa: E402

CG_ROOT = "/sys/fs/cgroup"
MODEL_BYTES = 143 * 2**20
NEED_BYTES = 2 * MODEL_BYTES + 150 * 2**20  # model, ORT's working copy and arena, interpreter: an assumption, named here


def _num(s):
    s = (s or "").strip()
    return int(s) if re.fullmatch(r"-?\d+", s) else None


def cpus_v2(s):
    """cpu.max: '<quota|max> [period]'. Returns CPUs or None for unlimited or unreadable."""
    parts = (s or "").split()
    if not parts or parts[0] == "max":
        return None
    q = _num(parts[0])
    per = _num(parts[1]) if len(parts) > 1 else 100000
    return q / per if q and per and q > 0 and per > 0 else None


def cpus_v1(quota, period):
    q, per = _num(quota), _num(period)
    return q / per if q and per and q > 0 and per > 0 else None  # quota -1 is unlimited


def mem_limit(s):
    """memory.max / memory.limit_in_bytes. 'max' and v1's page-aligned LONG_MAX mean unlimited."""
    n = _num(s)
    return n if n is not None and 0 < n < 2**60 else None


def _tightest(vals):
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None


def _chain(rel):
    """The cgroup directory and every ancestor up to the root, never above it."""
    rel = posixpath.normpath("/" + (rel or "/").lstrip("/"))
    if rel.startswith("/.."):
        rel = "/"
    out = []
    while True:
        out.append(rel)
        if rel == "/":
            return out
        rel = posixpath.dirname(rel)


def cgroup_limits(read, proc_cgroup, affinity):
    """read(path) -> text or None. Returns dict(cpus, mem_limit, mem_used, version, threads)."""
    paths = {}
    for line in (proc_cgroup or "").splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3:
            for c in parts[1].split(","):
                paths[c] = parts[2]
    cpus, lim, used = None, None, None
    if read(CG_ROOT + "/cgroup.controllers") is not None:
        version = 2
        chain = _chain(paths.get("", "/"))
        cpus = _tightest(cpus_v2(read(posixpath.join(CG_ROOT, d.lstrip("/"), "cpu.max"))) for d in chain)
        lim = _tightest(mem_limit(read(posixpath.join(CG_ROOT, d.lstrip("/"), "memory.max"))) for d in chain)
        used = _num(read(posixpath.join(CG_ROOT, chain[0].lstrip("/"), "memory.current")))
    else:
        version = 1
        cdirs = _chain(paths.get("cpu", "/"))
        mdirs = _chain(paths.get("memory", "/"))
        cpus = _tightest(cpus_v1(read(posixpath.join(CG_ROOT, "cpu", d.lstrip("/"), "cpu.cfs_quota_us")),
                                 read(posixpath.join(CG_ROOT, "cpu", d.lstrip("/"), "cpu.cfs_period_us"))) for d in cdirs)
        lim = _tightest(mem_limit(read(posixpath.join(CG_ROOT, "memory", d.lstrip("/"), "memory.limit_in_bytes"))) for d in mdirs)
        used = _num(read(posixpath.join(CG_ROOT, "memory", mdirs[0].lstrip("/"), "memory.usage_in_bytes")))
    n_aff = max(1, len(affinity))
    threads = n_aff if cpus is None else max(1, min(n_aff, int(math.floor(cpus))))
    return {"cpus": cpus, "mem_limit": lim, "mem_used": used, "version": version, "threads": threads}


def memory_ok(lim, used, need=NEED_BYTES):
    return lim is None or lim - (used or 0) >= need


def _fs(files):
    return lambda p: files.get(p)


def sim_cgroup() -> dict:
    MiB = 2**20
    # --- primitives on sample contents
    for s, want in (("max 100000\n", None), ("200000 100000\n", 2.0), ("50000 100000", 0.5), ("150000 100000", 1.5), ("max", None),
                    ("100000", 1.0), ("", None), ("abc def", None), ("0 100000", None), ("-1 100000", None), ("100000 0", None),
                    ("1_0 100000", None), ("  300000   100000  \n", 3.0), (None, None)):
        assert cpus_v2(s) == want, f"cpu.max {s!r}: {cpus_v2(s)} != {want}"
    for q, per, want in (("-1\n", "100000\n", None), ("200000", "100000", 2.0), ("-1", "0", None), ("100000", "0", None), ("x", "y", None),
                         (None, None, None), ("25000", "100000", 0.25)):
        assert cpus_v1(q, per) == want, (q, per)
    for s, want in (("max\n", None), ("536870912\n", 512 * MiB), ("0", None), ("-1", None), ("9223372036854771712\n", None),
                    ("9223372036854775807", None), ("junk", None), ("", None), (None, None), ("1073741824", 1024 * MiB)):
        assert mem_limit(s) == want, f"memory limit {s!r}"

    cases = {}
    # --- cgroup v2, container cgroup at the mount root: 2 CPUs, 1 GiB
    v2 = {CG_ROOT + "/cgroup.controllers": "cpu memory\n", CG_ROOT + "/cpu.max": "200000 100000\n",
          CG_ROOT + "/memory.max": "1073741824\n", CG_ROOT + "/memory.current": "104857600\n"}
    cases["v2 root"] = (cgroup_limits(_fs(v2), "0::/\n", range(64)), 2, 1024 * MiB, 100 * MiB)
    # --- v2 nested: child unlimited, parent 2 CPUs / 512 MiB; and child tighter than parent
    kube = {CG_ROOT + "/cgroup.controllers": "cpu memory\n", CG_ROOT + "/k/p/cpu.max": "max 100000\n", CG_ROOT + "/k/cpu.max": "200000 100000\n",
            CG_ROOT + "/k/p/memory.max": "max\n", CG_ROOT + "/k/memory.max": "536870912\n", CG_ROOT + "/k/p/memory.current": "1000\n"}
    cases["v2 parent limits"] = (cgroup_limits(_fs(kube), "0::/k/p\n", range(16)), 2, 512 * MiB, 1000)
    kube2 = {**kube, CG_ROOT + "/k/p/cpu.max": "100000 100000\n", CG_ROOT + "/k/cpu.max": "400000 100000\n"}
    cases["v2 child tighter"] = (cgroup_limits(_fs(kube2), "0::/k/p\n", range(16)), 1, 512 * MiB, 1000)
    # --- v1 / hybrid (this container's layout: v1 controllers plus a '0::/' line), nested memory path, 'unlimited' sentinels
    v1 = {CG_ROOT + "/cpu/cpu.cfs_quota_us": "150000\n", CG_ROOT + "/cpu/cpu.cfs_period_us": "100000\n",
          CG_ROOT + "/memory/memory.limit_in_bytes": "9223372036854771712\n", CG_ROOT + "/memory/process_api/abc/memory.limit_in_bytes": "2147483648\n",
          CG_ROOT + "/memory/process_api/abc/memory.usage_in_bytes": "52428800\n", CG_ROOT + "/memory/process_api/memory.limit_in_bytes": "9223372036854771712\n"}
    proc_v1 = "9:name=systemd:/\n4:memory:/process_api/abc\n3:cpuset:/\n2:cpuacct:/\n1:cpu:/\n0::/\n"
    cases["v1 hybrid"] = (cgroup_limits(_fs(v1), proc_v1, range(8)), 1, 2048 * MiB, 50 * MiB)  # 1.5 CPUs floors to 1 thread
    v1u = {CG_ROOT + "/cpu/cpu.cfs_quota_us": "-1\n", CG_ROOT + "/cpu/cpu.cfs_period_us": "100000\n",
           CG_ROOT + "/memory/memory.limit_in_bytes": "9223372036854771712\n"}
    cases["v1 unlimited"] = (cgroup_limits(_fs(v1u), "4:memory:/\n1:cpu,cpuacct:/\n", range(8)), 8, None, None)
    # --- cpuset narrower than the host, with and without a quota
    cases["cpuset 4 of 64, no quota"] = (cgroup_limits(_fs(v1u), "1:cpu:/\n", range(4)), 4, None, None)
    q8 = {CG_ROOT + "/cgroup.controllers": "", CG_ROOT + "/cpu.max": "800000 100000\n"}
    cases["quota above cpuset"] = (cgroup_limits(_fs(q8), "0::/\n", range(4)), 4, None, None)
    q05 = {CG_ROOT + "/cgroup.controllers": "", CG_ROOT + "/cpu.max": "50000 100000\n"}
    cases["half a CPU"] = (cgroup_limits(_fs(q05), "0::/\n", range(64)), 1, None, None)
    # --- nothing readable, garbage everywhere, an empty affinity: fail open to the affinity (or 1), never raise
    cases["no cgroup files"] = (cgroup_limits(_fs({}), "", range(6)), 6, None, None)
    junk = {CG_ROOT + "/cpu/cpu.cfs_quota_us": "zzz", CG_ROOT + "/memory/memory.limit_in_bytes": "\x00\x00"}
    cases["garbage"] = (cgroup_limits(_fs(junk), "garbage line\n::\n1:cpu:/../../etc\n", range(3)), 3, None, None)
    cases["empty affinity"] = (cgroup_limits(_fs({}), "", []), 1, None, None)
    for name, (got, threads, lim, used) in cases.items():
        assert (got["threads"], got["mem_limit"], got["mem_used"]) == (threads, lim, used), f"{name}: {got}"
    assert cases["v2 root"][0]["version"] == 2 and cases["v1 hybrid"][0]["version"] == 1

    # (a) saving: the oversubscription the idea is about. ORT's default is the host's core count; the quota is what runs.
    quota_cpus, host_cores = 2.0, 64
    d = cgroup_limits(_fs(v2), "0::/\n", range(host_cores))
    assert host_cores / quota_cpus == 32 and d["threads"] / quota_cpus == 1.0, "32x oversubscription becomes 1x"

    # (c) must NOT apply: no limit means no reduction below the affinity mask
    assert cases["no cgroup files"][0]["threads"] == 6 and memory_ok(None, None)

    # memory guard, and what the hook does when it says no (real hook.run: the original goes through untouched)
    assert not memory_ok(512 * MiB, 100 * MiB) and memory_ok(1024 * MiB, 100 * MiB) and memory_ok(None, 5 * 2**40)
    assert memory_ok(1024 * MiB, None) and not memory_ok(1024 * MiB, 2000 * MiB) and not memory_ok(NEED_BYTES - 1, 0)
    hook = _ctx().hook
    saved = hook._compress
    loads = []

    def guarded(texts, rate, lim=512 * MiB, used=100 * MiB):
        if not memory_ok(lim, used):
            return list(texts)  # refuse to load the model here: hand the text back, hook.run then leaves the output alone
        loads.append(1)
        return [t[: len(t) // 2] for t in texts]

    hook._compress = guarded
    try:
        with tempfile.TemporaryDirectory() as tmp, _env(XDG_CACHE_HOME=tmp):
            ev = _event("the build step finished with a warning " * 200)
            assert hook.run(ev, 0.5, 6000) is None and not loads, "under a too-low memory.max: no load, Claude gets the original"
            hook._compress = lambda texts, rate: guarded(texts, rate, lim=4096 * MiB)
            assert hook.run(ev, 0.5, 6000) is not None and loads, "enough headroom: normal path"
    finally:
        hook._compress = saved

    # thread count changes how the windows are split, never the scores
    wins = _windows("t", 40)

    def score_with(threads):
        parts = [wins[i::threads] for i in range(threads)]
        res = {}
        for k, part in enumerate(parts):
            for j, w in enumerate(part):
                res[k + j * threads] = _score_row(w)
        return [res[i] for i in range(len(wins))]

    assert score_with(1) == score_with(2) == score_with(64) == [_score_row(w) for w in wins]

    # (d) the real files: must never raise, whatever this host looks like
    def real_read(p):
        try:
            return open(p).read()
        except OSError:
            return None

    aff = os.sched_getaffinity(0) if hasattr(os, "sched_getaffinity") else range(os.cpu_count() or 1)
    got = cgroup_limits(real_read, real_read("/proc/self/cgroup"), aff)
    assert 1 <= got["threads"] <= max(1, len(aff))
    return {"threads_default_vs_derived": f"{host_cores} vs {d['threads']}", "oversubscription_default": host_cores / quota_cpus,
            "oversubscription_derived": d["threads"] / quota_cpus, "cgroup_layouts_checked": len(cases), "real_host_threads": got["threads"]}


case(
    1199,
    claim="From sample cgroup v2, v1 and hybrid file contents (including 'max', -1, the v1 LONG_MAX sentinel, zero periods, garbage and nested cgroups where the tightest ancestor wins) intra-op threads become min(affinity, floor(quota)) with a floor of 1 and the memory guard refuses an in-process model load that would not fit, in which case hook.run hands the original through; with no limits nothing is reduced, and unreadable files fall back to the affinity mask.",
    sim=sim_cgroup,
)

# ===============================================================================================================
# #1378 Learned bail-out predictor gates the model path from a ledger.
#
# Ground truth comes from the real Compressor.compress (force_protected, rate 0.5) on a fake scorer, so a "bail" is exactly the
# hook's `after >= 0.95 * before`. Features are cheap (no atomizer): zlib ratio, unique-line share, character-class shares, mean
# line length. The tree is CART, depth 3, fitted on a canonically sorted ledger so row order cannot matter. The gate is
# deliberately stricter than the tree: it skips only in a pure all-bail leaf with enough rows, only for a command family seen
# enough times, only when the features are finite, and it always lets a deterministic 1-in-AUDIT sample through to the model.

FEATURE_NAMES = ("zlib", "uniq", "digit", "alpha", "space", "line")
MIN_LEAF, MIN_FAMILY, AUDIT = 25, 30, 20


def features(text):
    b = text.encode("utf-8", "surrogatepass")
    n = max(1, len(text))
    lines = text.split("\n")
    return (round(len(zlib.compress(b, 1)) / max(1, len(b)), 4), round(len(set(lines)) / len(lines), 4),
            round(sum(c.isdigit() for c in text) / n, 4), round(sum(c.isalpha() for c in text) / n, 4),
            round(sum(c.isspace() for c in text) / n, 4), round(min(len(text) / len(lines), 400) / 400, 4))


def _gini(nb, n):
    return 0.0 if n == 0 else 2 * (nb / n) * (1 - nb / n)


def _fit(rows, depth):
    n, nb = len(rows), sum(r[1] for r in rows)
    leaf = {"n": n, "bail": nb}
    if depth == 0 or nb in (0, n) or n < 2 * MIN_LEAF:
        return leaf
    best = None  # (impurity, feature, threshold); ties resolved by feature order then threshold: fully deterministic
    for fi in range(len(FEATURE_NAMES)):
        srt = sorted(rows, key=lambda r: (r[0][fi], r[1]))
        lb = 0
        for i in range(1, n):
            lb += srt[i - 1][1]
            if srt[i - 1][0][fi] == srt[i][0][fi] or i < MIN_LEAF or n - i < MIN_LEAF:
                continue
            imp = (i * _gini(lb, i) + (n - i) * _gini(nb - lb, n - i)) / n
            thr = round((srt[i - 1][0][fi] + srt[i][0][fi]) / 2, 6)
            if best is None or imp < best[0] - 1e-12:
                best = (imp, fi, thr)
    if best is None:
        return leaf
    _, fi, thr = best
    left = [r for r in rows if r[0][fi] <= thr]
    right = [r for r in rows if r[0][fi] > thr]
    return {"f": fi, "t": thr, "l": _fit(left, depth - 1), "r": _fit(right, depth - 1)}


def fit_predictor(ledger):
    """ledger: [(family, feature tuple, bailed)]. Returns a JSON-serialisable model; identical for any row order."""
    rows = sorted(((tuple(f), bool(b)) for _, f, b in ledger), key=lambda r: (r[0], r[1]))
    fam = {}
    for f, _, _ in ledger:
        fam[f] = fam.get(f, 0) + 1
    return {"tree": _fit(rows, 3), "families": dict(sorted(fam.items()))}


def _tree_says_bail(model, text):
    f = features(text)
    if not all(math.isfinite(x) for x in f):
        return False
    node = model["tree"]
    while "f" in node:
        node = node["l"] if f[node["f"]] <= node["t"] else node["r"]
    return node["n"] >= MIN_LEAF and node["bail"] == node["n"]


def should_skip(model, family, text):
    """True only when skipping the model is safe by every rule. Any error or doubt returns False (run the model)."""
    try:
        if family in model.get("veto", ()):
            return False  # an audited call recently contradicted the tree for this family
        if model["families"].get(family, 0) < MIN_FAMILY:
            return False  # a command family it has not seen enough of
        if zlib.crc32(text.encode("utf-8", "surrogatepass")) % AUDIT == 0:
            return False  # deterministic audit sample: the model still runs, so drift shows up
        return _tree_says_bail(model, text)
    except Exception:  # noqa: BLE001  corrupt model file, odd input: fail open
        return False


def after_model_ran(model, ledger, family, text, ratio):
    """Call whenever the model actually ran. Logs the row; vetoes the family at once if the tree would have skipped this input
    but it did not bail (drift); lifts the veto after MIN_FAMILY consecutive bails of that family have been logged."""
    try:
        bailed = ratio >= 0.95
        veto = set(model.get("veto", ()))
        if not bailed and _tree_says_bail(model, text):
            veto.add(family)
        ledger.append((family, features(text), bailed))
        if family in veto:
            rows = [b for f, _, b in ledger if f == family][-MIN_FAMILY:]
            if len(rows) == MIN_FAMILY and all(rows):
                veto.discard(family)
        model["veto"] = sorted(veto)
    except Exception:  # noqa: BLE001
        pass


def _gen(kind, rng):
    if kind == "env":
        return _env_dump(rng.randrange(14, 30), rng)
    if kind == "seq":
        return "".join(f"{i}\n" for i in range(rng.randrange(1, 50), rng.randrange(250, 500)))
    if kind == "ls":
        return _ls_lR(rng.randrange(24, 48), rng)
    if kind == "make":
        return _prose_log(rng.randrange(20, 40), rng)
    if kind == "psql":
        return _numeric_table(rng.randrange(40, 90), rng)
    if kind == "pytest":
        return "".join(f"tests/test_mod{i % 9}.py::test_case_{i} {rng.choice(['PASSED', 'PASSED', 'FAILED'])} [{i * 100 // 60:3d}%]\n" for i in range(60)) \
            + _prose_log(rng.randrange(0, 8), rng)
    raise ValueError(kind)


def sim_learned_bailout() -> dict:
    fc = _fake_compressor("1378")

    def ground_truth(text):
        out = fc.compress(text, rate=0.5, force_protected=True)["text"]
        return len(out) / len(text)

    def make(counts, seed):
        rng = random.Random(seed)
        rows = []
        for kind, n in counts.items():
            for _ in range(n):
                t = _gen(kind, rng)
                rows.append((kind, t, ground_truth(t)))
        return rows

    train = make({"env": 60, "seq": 60, "ls": 40, "make": 40, "psql": 8, "pytest": 40}, 1)
    test = make({"env": 40, "seq": 40, "ls": 30, "make": 30, "psql": 20, "pytest": 30}, 2)
    ledger = [(k, features(t), r >= 0.95) for k, t, r in train]
    model = fit_predictor(ledger)
    assert len(train) + len(test) < 500 and any(b for _, _, b in ledger) and not all(b for _, _, b in ledger)

    # deterministic: same ledger, any order, any number of fits -> byte-identical model
    want = json.dumps(model, sort_keys=True)
    for seed in range(6):
        shuffled = ledger[:]
        random.Random(seed).shuffle(shuffled)
        assert json.dumps(fit_predictor(shuffled), sort_keys=True) == want, "fit depends on ledger order"
    assert json.dumps(fit_predictor(ledger), sort_keys=True) == want

    # (a) saving, and (b) reader view: every skipped input is one the full path would have bailed on (Claude gets the original anyway)
    skipped = bails = false_strict = false_idea = audited_skips = 0
    per_family = {}
    for kind, text, ratio in test:
        bail = ratio >= 0.95
        bails += bail
        sk = should_skip(model, kind, text)
        skipped += sk
        false_strict += sk and not bail
        false_idea += sk and ratio < 0.90  # the idea's definition: predicted bail but the model would cut over 10 percent
        per_family.setdefault(kind, [0, 0])
        per_family[kind][0] += sk
        per_family[kind][1] += 1
    assert false_strict == 0, f"{false_strict} skipped inputs the model would have compressed"
    assert skipped >= 0.4 * bails > 0, f"predictor skips {skipped} of {bails} bails: the saving must be real"
    assert per_family["ls"][0] == 0 and per_family["make"][0] == 0, "must never skip the inputs the model compresses well"
    assert per_family["psql"][0] == 0, "psql was seen 8 times (< MIN_FAMILY): never skipped"

    # (c) must NOT apply: unseen or rarely seen classes, with the guard shown to be the thing that prevents it
    rng = random.Random(7)
    env_like = [_env_dump(rng.randrange(14, 30), rng) for _ in range(30)]
    assert sum(should_skip(model, "env", t) for t in env_like) >= 15, "sanity: these inputs are skippable when the family is known"
    assert not any(should_skip(model, "journalctl", t) for t in env_like), "unseen family must never be skipped"
    assert not any(should_skip(model, "psql", _numeric_table(60, rng)) for _ in range(10)), "rare family must never be skipped"
    empty = fit_predictor([])
    assert not any(should_skip(empty, "env", t) for t in env_like), "an empty ledger skips nothing"
    thin = [r for k in ("env", "seq", "ls", "make", "psql", "pytest") for r in [x for x in ledger if x[0] == k][:MIN_FAMILY - 1]]
    assert not any(should_skip(fit_predictor(thin), k, t) for k, t, _ in test), "fewer than MIN_FAMILY rows per family skips nothing"
    stream_rng = random.Random(13)
    stream = [_env_dump(stream_rng.randrange(14, 30), stream_rng) for _ in range(200)]
    audited = [t for t in stream if zlib.crc32(t.encode()) % AUDIT == 0]
    assert audited and not any(should_skip(model, "env", t) for t in audited), "the audit sample exists and always reaches the model"
    assert sum(should_skip(model, "env", t) for t in stream) >= 0.8 * (len(stream) - len(audited)), "everything else in the stream is skipped"

    # drift: the hook's behaviour changes (force_protected switched off), so env output stops bailing, and later changes back.
    # The stale tree keeps skipping until the first audited call shows the model compressing; that call vetoes the family at once.
    drifted_ratio = lambda t: len(fc.compress(t, rate=0.5, force_protected=False)["text"]) / len(t)  # noqa: E731
    live, live_ledger = json.loads(json.dumps(model)), list(ledger)
    phase_rng = random.Random(17)
    phase = lambda n: [_env_dump(phase_rng.randrange(14, 30), phase_rng) for _ in range(n)]  # noqa: E731
    drift_texts, recovered = phase(150), phase(150)
    assert drifted_ratio(drift_texts[0]) < 0.9, "sanity: after the change the model really does compress this"
    wrong, first_audit_index = 0, None
    for i, t in enumerate(drift_texts):
        if should_skip(live, "env", t):
            wrong += 1  # skipped, but the full path would now have compressed it
        else:
            first_audit_index = i if first_audit_index is None else first_audit_index
            after_model_ran(live, live_ledger, "env", t, drifted_ratio(t))
    audits_before_detection = sum(zlib.crc32(t.encode()) % AUDIT != 0 for t in drift_texts[:first_audit_index])
    assert "env" in live["veto"], "an audited call must reveal the drift"
    assert wrong == audits_before_detection <= first_audit_index, "all wrong skips happen before the first audited call; none after"
    skips_c = runs_c = 0
    for t in recovered:  # behaviour restored: bails again. While vetoed the model runs and is logged; then skipping resumes.
        if should_skip(live, "env", t):
            skips_c += 1
        else:
            runs_c += 1
            after_model_ran(live, live_ledger, "env", t, ground_truth(t))
    assert "env" not in live["veto"] and skips_c >= 60 and runs_c >= MIN_FAMILY, (live["veto"], skips_c, runs_c)

    # (d) failure handling: corrupt or hostile model and inputs never raise and never skip
    for bad in (None, {}, {"tree": 5, "families": {"env": 99}}, {"tree": {"f": 99, "t": 0, "l": {}, "r": {}}, "families": {"env": 99}},
                {"tree": {"n": 40, "bail": 40}, "families": {"env": "many"}}, "garbage"):
        assert should_skip(bad, "env", "A=/x\n" * 100) is False
    for odd in (None, 5, b"bytes", "", "\n", "\ud800" * 50, "x" * 3):
        should_skip(model, "env", odd)  # must not raise (result is irrelevant here)
    assert fit_predictor([("env", features(""), True)] * 3) is not None, "fitting on junk must not raise"

    secs = skipped * 2.0  # the ~2 s model load AGENTS.md quotes, per skipped call
    return {"test_inputs": len(test), "bails": bails, "skipped": skipped, "false_skips_strict": false_strict,
            "false_skips_idea_def_cut_over_10pct": false_idea, "seconds_saved_at_2s_per_call": secs,
            "skip_share_of_bails": round(skipped / bails, 2), "wrong_skips_before_drift_detected": wrong, "skips_after_recovery": skips_c,
            "skips_by_family": {k: f"{a}/{b}" for k, (a, b) in sorted(per_family.items())}}


case(
    1378,
    claim="A depth-3 tree over cheap features, fitted on a canonically ordered ledger, produces a byte-identical model for any row order and skips the model only in a pure all-bail leaf for a command family seen at least 30 times (never on unseen or rare families, an empty or tiny ledger, a deterministic audit sample, or any error), with zero false skips and a real share of the bail-outs skipped on held-out inputs, and after drift the first audited contradiction vetoes the family until 30 consecutive bails are logged again.",
    sim=sim_learned_bailout,
)

# --- END ---
