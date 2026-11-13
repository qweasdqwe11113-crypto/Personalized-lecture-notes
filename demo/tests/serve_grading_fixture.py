"""Disposable grading UI with deterministic answers; never calls real models."""
from pathlib import Path
import json
import tempfile
from threading import Lock
import time

from flask import Flask
from werkzeug.serving import make_server
from test_grading import result_fixture
import app as module
from grading import register_grading
from qwen import ModelResult


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2]
    stop = root / '.runtime' / 'stop-grading-fixture'
    stop.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        fixture = Flask('isolated-grading')
        def model(prepared, attempt):
            attempt()
            return ModelResult(json.dumps(result_fixture(), ensure_ascii=False))
        register_grading(fixture, directory, model, Lock())
        for name in fixture.view_functions:
            if name != 'static':
                module.app.view_functions[name] = fixture.view_functions[name]
        server = make_server('127.0.0.1',5056,module.app,threaded=True)
        server.timeout = 1
        try:
            print('Disposable grading UI: http://127.0.0.1:5056/#grading',flush=True)
            deadline = time.monotonic() + 600
            while not stop.exists() and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()
            stop.unlink(missing_ok=True)
