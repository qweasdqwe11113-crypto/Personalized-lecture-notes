"""In-memory live text of running generations, for the browser's streaming preview.

This is display state only: it is never saved, never read back into a lecture, and a
restart simply loses it. The saved lecture still comes from the checked final output.
"""
from threading import Condition
import time

KEEP_SECONDS = 120


class ProgressHub:
    def __init__(self):
        self._condition = Condition()
        self._runs = {}

    def start(self, run_id, parts=()):
        """parts: ordered (key, heading) pairs; a single-call run uses one part."""
        with self._condition:
            self._purge()
            self._runs[run_id] = dict(version=1, stage='准备中', done=False, finished_at=None,
                                      order=[key for key, _ in parts],
                                      parts={key: dict(heading=heading, text='', version=0, state='waiting')
                                             for key, heading in parts})
            self._condition.notify_all()

    def _touch(self, run):
        run['version'] += 1
        self._condition.notify_all()

    def stage(self, run_id, label):
        with self._condition:
            run = self._runs.get(run_id)
            if run:
                run['stage'] = label
                self._touch(run)

    def write(self, run_id, key, text, state='writing'):
        with self._condition:
            run = self._runs.get(run_id)
            if not run:
                return
            part = run['parts'].setdefault(key, dict(heading='', text='', version=0, state='waiting'))
            if key not in run['order']:
                run['order'].append(key)
            part['text'], part['state'] = text, state
            self._touch(run)
            part['version'] = run['version']

    def finish(self, run_id):
        with self._condition:
            run = self._runs.get(run_id)
            if run:
                run['done'], run['finished_at'] = True, time.monotonic()
                self._touch(run)

    def _purge(self):
        now = time.monotonic()
        for key in [k for k, r in self._runs.items() if r['done'] and now - r['finished_at'] > KEEP_SECONDS]:
            del self._runs[key]

    def changes(self, run_id, since=0, timeout=0):
        """Parts changed after version `since`, or None if nothing changed within timeout."""
        with self._condition:
            def ready():
                run = self._runs.get(run_id)
                return run is not None and run['version'] > since
            if not ready() and (not timeout or not self._condition.wait_for(ready, timeout)):
                return None
            run = self._runs[run_id]
            return dict(version=run['version'], stage=run['stage'], done=run['done'], order=list(run['order']),
                        parts=[dict(key=key, heading=run['parts'][key]['heading'], text=run['parts'][key]['text'],
                                    state=run['parts'][key]['state'])
                               for key in run['order'] if run['parts'][key]['version'] > since])


HUB = ProgressHub()
