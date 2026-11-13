"""Bounded model concurrency shared by every language-model caller in this process.

It keeps the small surface the old single Lock had (acquire, release, locked, context
manager), so callers that only need one call at a time did not change. With a limit of
1 the behaviour is exactly the old global lock.
"""
import os
from threading import Condition

MAX_LIMIT = 8


def configured_limit(default=3):
    try:
        return min(MAX_LIMIT, max(1, int(os.environ.get('MODEL_CONCURRENCY', default))))
    except ValueError:
        return default


class ModelGate:
    def __init__(self, limit=1):
        self._condition = Condition()
        self._in_use = 0
        self.limit = min(MAX_LIMIT, max(1, int(limit)))

    def acquire(self, blocking=True, timeout=-1):
        with self._condition:
            if not blocking:
                if self._in_use >= self.limit:
                    return False
            elif not self._condition.wait_for(lambda: self._in_use < self.limit,
                                              None if timeout is None or timeout < 0 else timeout):
                return False
            self._in_use += 1
            return True

    def release(self):
        with self._condition:
            if self._in_use <= 0:
                raise RuntimeError('release of an unacquired model permit')
            self._in_use -= 1
            self._condition.notify_all()

    def locked(self):
        """True while any model call is in flight; used for "is everything idle" checks."""
        with self._condition:
            return self._in_use > 0

    def in_use(self):
        with self._condition:
            return self._in_use

    def set_limit(self, limit):
        with self._condition:
            self.limit = min(MAX_LIMIT, max(1, int(limit)))
            self._condition.notify_all()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()
