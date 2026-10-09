"""Per-recording locks for the in-memory beat state.

FastAPI runs these sync endpoints in a threadpool, so detection, arrhythmia
runs, manual edits, review actions and persistence for the same recording
really do overlap. Every one of them that READS `beat_cache[path]`, works on
it, and WRITES a result back must hold `path_lock(path)` for that whole
read-modify-write; otherwise a slow writer (an arrhythmia run takes seconds)
overwrites an edit made while it computed, silently resurrecting a deleted
beat or dropping a review.

Rules that keep this deadlock-free:
- hold at most ONE path's lock at a time, and never call into code that takes
  a different path's lock while holding one;
- take the path lock BEFORE opening a SQLite connection / starting a write
  transaction, never while already holding one;
- read-only endpoints (the window routes) stay lock-free: they read a frame
  that is never mutated after it is published, only replaced.
"""

import threading

_registry: dict[str, threading.RLock] = {}
_registry_lock = threading.Lock()


def path_lock(path: str) -> threading.RLock:
    """The re-entrant lock for `path`, created on first use.

    Locks are never removed from the registry (one small object per distinct
    path): dropping one while a thread still waits on it would let two
    threads hold different locks for the same recording.
    """
    with _registry_lock:
        lock = _registry.get(path)
        if lock is None:
            lock = _registry[path] = threading.RLock()
        return lock
