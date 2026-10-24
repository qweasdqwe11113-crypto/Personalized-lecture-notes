"""Disposable preparation workflow UI; deterministic responses, never a real model."""
import os
import signal

from werkzeug.serving import make_server
from test_preparation_workflow import PreparationWorkflowTests, module


def stop(_signum, _frame):
    raise KeyboardInterrupt


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, stop)
    PreparationWorkflowTests.setUpClass()
    fixture = PreparationWorkflowTests()
    fixture.setUp()
    def model(prepared, on_attempt):
        on_attempt()
        return module.ModelResult('# 备课流程验证讲义\n\n## 学习目标\n这是隔离测试响应，不是真实模型输出。\n\n## 本次学习安排\n依据所选备课副本、学生档案与本次备注组织讲解。\n\n## 核心讲解\n验证正文展示和输入追溯。\n\n## 例题与检验\n此处为确定性测试示例。\n\n## 易错点与小结\n参考材料不足，需教师核对。')
    module.call_qwen = model
    module.MODEL = 'fixture-preparation-only'
    os.environ['QWEN_API_KEY'] = 'fixture-only'
    os.environ['QWEN_BASE_URL'] = 'https://fixture.invalid/v1'
    server = make_server('127.0.0.1', 5056, module.app, threaded=True)
    try:
        print('Isolated preparation UI: http://127.0.0.1:5056; schema=' + fixture.schema, flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        fixture.tearDown()
        PreparationWorkflowTests.tearDownClass()
