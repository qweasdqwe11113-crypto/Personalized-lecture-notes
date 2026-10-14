"""Serve a temporary UI fixture at 5056; delete its schema when stopped.

For browser QA only. Uses the dedicated demo database, never its public data.
"""
import signal

from werkzeug.serving import make_server

from test_profiles import ProfileIntegrationTests, module


def disabled_model(_prepared, _on_attempt):
    raise module.ModelError('fixture_disabled', '这是隔离界面测试，不调用 Qwen。请在正式 demo 中生成讲义。', 'configuration')


def stop_fixture(_signum, _frame):
    # Also run finally when a terminal/session manager sends SIGTERM.
    raise KeyboardInterrupt


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, stop_fixture)
    ProfileIntegrationTests.setUpClass()
    server = None
    try:
        fixture = ProfileIntegrationTests()
        fixture.setUp()
        module.call_qwen = disabled_model
        module.app.testing = False
        server = make_server('127.0.0.1', 5056, module.app, threaded=True)
        print('隔离 UI 测试：http://127.0.0.1:5056；不写入原业务表，不调用 Qwen。', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        ProfileIntegrationTests.tearDownClass()
