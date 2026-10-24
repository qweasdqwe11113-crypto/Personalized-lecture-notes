"""Disposable UI at 5056 with deterministic question fixtures; no model calls."""
import json
from pathlib import Path
import time

from werkzeug.serving import make_server
from test_quizzes import QuizTests, fixture_result, module


if __name__ == '__main__':
    stop = Path(__file__).resolve().parents[2] / '.runtime' / 'stop-quiz-fixture'
    stop.unlink(missing_ok=True)
    QuizTests.setUpClass()
    fixture = QuizTests()
    fixture.setUp()
    def model(prepared, attempted):
        attempted()
        time.sleep(0.5)
        context = json.loads(json.loads(prepared.body)['messages'][1]['content'])['context']
        return module.ModelResult(json.dumps(fixture_result(context), ensure_ascii=False), {'total_tokens': 10})
    module.call_qwen = model
    server = make_server('127.0.0.1', 5056, module.app, threaded=True)
    server.timeout = 1
    try:
        print('Isolated quiz UI: http://127.0.0.1:5056/#quizzes; schema='+fixture.schema, flush=True)
        deadline = time.monotonic()+1200
        while not stop.exists() and time.monotonic()<deadline:
            server.handle_request()
    finally:
        server.server_close()
        fixture.tearDown()
        QuizTests.tearDownClass()
        stop.unlink(missing_ok=True)
