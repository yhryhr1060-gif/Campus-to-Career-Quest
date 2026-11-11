"""Serialize a session's in-flight game actions in the local single-process server.

A disconnected browser does not cancel a model call. Its retry must wait and then see
the first request's saved result. RLock allows retry() to call start() safely.
"""
from functools import wraps
from threading import Lock, RLock
from weakref import WeakValueDictionary

_guard = Lock()
_locks = WeakValueDictionary()


def serialized_session(operation):
    @wraps(operation)
    def guarded(sid, *args, **kwargs):
        with _guard:
            lock = _locks.get(sid)
            if lock is None:
                lock = RLock()
                _locks[sid] = lock
        with lock:
            return operation(sid, *args, **kwargs)
    return guarded
