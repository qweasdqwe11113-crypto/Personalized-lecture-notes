"""Disposable 5056 UI fixture with slow deterministic worker; never calls Qwen."""
import os
import signal
import time
from werkzeug.serving import make_server
from test_courses_jobs import CourseJobTests, module


def stop(_signum, _frame):
    raise KeyboardInterrupt


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, stop)
    CourseJobTests.setUpClass()
    fixture = CourseJobTests()
    fixture.setUp()
    seen_failures = set()
    def model(prepared, on_attempt):
        on_attempt()
        time.sleep(10)
        if '模拟失败' in prepared.body and prepared.sha256 not in seen_failures:
            seen_failures.add(prepared.sha256)
            raise module.ModelError('fixture_timeout', '隔离测试模拟超时，可按原输入重试。')
        return module.ModelResult('# 隔离 UI 概率讲义\n\n## 学习目标\n这只是测试响应，不是真实 Qwen 输出。\n\n## 本次学习安排\n验证排队、完成与恢复查看。\n\n## 核心讲解\nP(A|B) = P(A∩B) / P(B) [M11]\n条件为 P(B)>0。\n\n## 例题与检验\nP(D) = 0.6 × 0.02 + 0.4 × 0.05 = 0.032 [M12]\n\n## 易错点与小结\n互斥完备划分，后验和为 1 [M13]', {'prompt_tokens': 100, 'completion_tokens': 80, 'total_tokens': 180})
    module.call_qwen = model
    os.environ['QWEN_API_KEY'] = 'fixture-only'
    os.environ['QWEN_BASE_URL'] = 'https://fixture.invalid/v1'
    module.app.testing = False
    server = make_server('127.0.0.1', 5056, module.app, threaded=True)
    try:
        print('隔离大学课程与队列 UI：http://127.0.0.1:5056；模型是 10 秒测试响应。', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        fixture.tearDown()
        CourseJobTests.tearDownClass()
