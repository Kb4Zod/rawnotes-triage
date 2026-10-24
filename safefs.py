"""File-system safety helpers for rawnotes-triage. Standard library only, no global state of the app.

- ensure_inside: the one rule for where the app may write (inside a trusted root, never through a symlink)
- atomic_write: write a temp file next to the target and rename it over, keeping the target's mode
- RWLock: many readers or one writer (writers first), re-entrant for the writing thread
- StatCache: remember something derived from a file until the file changes
"""
import contextlib, os, stat, threading, time


def is_link(path):
    """A symlink, or on Windows a junction: the link anyone can make there without admin rights, which
    os.path.islink doesn't report. Both can point anywhere, so both count as links."""
    return os.path.islink(path) or getattr(os.path, "isjunction", lambda p: False)(path)


def ensure_inside(roots, path):
    """The normalised absolute `path`, if it lies under one of `roots` and neither it nor any folder between the
    root and it is a symlink. The root itself may be a symlink (that is the user's own choice).
    Raises ValueError otherwise. Missing components are fine: that is how new files are checked."""
    path = os.path.normpath(os.path.abspath(path))
    best = None
    for root in roots:
        root = os.path.normpath(os.path.abspath(root))
        if path == root or path.startswith(root.rstrip(os.sep) + os.sep):
            if best is None or len(root) > len(best): best = root
    if best is None: raise ValueError("refusing to touch a path outside the notes and projects folders")
    cur = best
    for part in [p for p in path[len(best):].split(os.sep) if p]:
        cur = os.path.join(cur, part)
        if is_link(cur): raise ValueError(f"refusing to write through a symlink ({os.path.basename(cur)})")
    return path


def atomic_write(path, data, mode=None):
    """Replace `path` with `data` (bytes) in one step: temp file in the same folder, fsync, rename over.
    Keeps the existing file's permission bits (or `mode`); a new file gets the usual umask default.
    A failure leaves the old file untouched and no temp file behind."""
    d, base = os.path.split(path)
    if mode is None:
        try: mode = stat.S_IMODE(os.stat(path).st_mode)
        except FileNotFoundError: mode = None
    tmp = os.path.join(d, f".{base}.triage-{os.getpid()}-{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o666)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data); fh.flush(); os.fsync(fh.fileno())
        if mode is not None: os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError): os.unlink(tmp)
        raise


class RWLock:
    """Readers share, a writer is alone. Waiting writers hold new readers back so writes are not starved.
    The writing thread may take the write or the read side again (nested calls); a thread already reading may
    read again."""
    def __init__(self):
        self._cv = threading.Condition(); self._readers = 0; self._writer = None; self._depth = 0; self._waiting = 0
        self._local = threading.local()

    @contextlib.contextmanager
    def read(self):
        me = threading.get_ident(); mine = getattr(self._local, "reads", 0)
        if self._writer == me or mine:  # nested: never wait on ourselves
            self._local.reads = mine + 1
            try: yield
            finally: self._local.reads -= 1
            return
        with self._cv:
            while self._writer is not None or self._waiting: self._cv.wait()
            self._readers += 1
        self._local.reads = 1
        try: yield
        finally:
            self._local.reads = 0
            with self._cv:
                self._readers -= 1
                if not self._readers: self._cv.notify_all()

    @contextlib.contextmanager
    def write(self):
        me = threading.get_ident()
        with self._cv:
            if self._writer == me: self._depth += 1
            else:
                self._waiting += 1
                try:
                    while self._writer is not None or self._readers: self._cv.wait()
                finally: self._waiting -= 1
                self._writer = me; self._depth = 1
        try: yield
        finally:
            with self._cv:
                self._depth -= 1
                if not self._depth: self._writer = None; self._cv.notify_all()


class StatCache:
    """value = get(ident, paths, build): build(paths) runs only when the stat of any path changed.
    A file modified within `racy` seconds of being read is not trusted yet (a later edit in the same timestamp tick
    could leave size and mtime identical), so it is read again next time. Bounded: dropped wholesale at `limit`."""
    def __init__(self, limit=6000, racy=2.0):
        self._d = {}; self._lock = threading.Lock(); self.limit = limit; self.racy = racy; self.hits = 0; self.builds = 0

    @staticmethod
    def _key(paths):
        out = []
        for p in paths:
            try: st = os.stat(p)
            except OSError: out.append(None); continue
            out.append((st.st_mtime_ns, st.st_size, st.st_ino, st.st_ctime_ns))
        return tuple(out)

    def get(self, ident, paths, build):
        key = self._key(paths)
        with self._lock: hit = self._d.get(ident)
        if hit and hit[0] == key and hit[2]:
            self.hits += 1; return hit[1]
        value = build(paths)
        now = time.time_ns(); limit = int(self.racy * 1e9)
        trusted = all(k is None or 0 <= now - k[0] > limit for k in key)
        with self._lock:
            if len(self._d) >= self.limit: self._d.clear()
            self._d[ident] = (key, value, trusted); self.builds += 1
        return value

    def clear(self):
        with self._lock: self._d.clear()
