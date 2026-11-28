"""Disposable UI on 5058 for the one-click lesson (一键备课) and the anchored material panel. Touch
.runtime/stop-quick-fixture to stop and clean up.

One course (大学概率，隔离测试) with six knowledge points in three chapters and a class of three students;
offline hashing vectors stand in for the embedding model. No language model is called: generating a lecture
here fails on purpose. The temporary schema is dropped on exit.
"""
import os
from pathlib import Path
import time

from werkzeug.serving import make_server
from test_profiles import ProfileDatabaseFixture  # puts demo/ on sys.path
from test_retrieval import seed_university
import test_hybrid_retrieval as hybrid_case
from test_quick_start import CHAPTERS
import app as module
from embed import HashingEmbedder

MORE = ((20, 16, '应用题：检测呈阳性时真正患病的概率。先用全概率公式求 P(阳性)，再用贝叶斯公式求后验；'
                 'P(患病|阳性) 与 P(阳性|患病) 的方向不同，不能互换。'),
        (21, 16, '应用题：三条产线的次品来源。用贝叶斯公式分别求次品来自各产线的后验概率，并检查三个后验之和为 1。'))


def seed(fixture):
    seed_university(fixture)
    with fixture.fixture_db() as conn:
        for kid, name, content in hybrid_case.COURSE:
            conn.execute('INSERT INTO knowledge_points VALUES (%s,%s)', (kid, name))
            conn.execute('INSERT INTO materials VALUES (%s,%s,%s,%s,%s,%s,%s)',
                         (kid, kid, name, content, '隔离测试资料', f'https://example.invalid/{kid}', '自编'))
        for mid, kid, content in MORE:
            conn.execute('INSERT INTO materials VALUES (%s,%s,%s,%s,%s,%s,%s)',
                         (mid, kid, f'应用题 {mid}', content, '隔离测试资料', f'https://example.invalid/{mid}', '自编'))
        course = conn.execute("INSERT INTO courses(name) VALUES ('大学概率（隔离测试）') RETURNING id").fetchone()['id']
        klass = conn.execute("INSERT INTO classes(course_id,name) VALUES (%s,'一班') RETURNING id", (course,)).fetchone()['id']
        for sid in (11, 12, 13):
            conn.execute('INSERT INTO class_students(class_id,course_id,student_id) VALUES (%s,%s,%s)', (klass, course, sid))
        for kid, (chapter, position) in CHAPTERS.items():
            conn.execute('UPDATE knowledge_points SET course_id=%s, chapter=%s, position=%s WHERE id=%s',
                         (course, chapter, position, kid))


def no_model(prepared, attempted):
    raise RuntimeError('隔离测试：这个界面不调用语言模型。')


if __name__ == '__main__':
    stop = Path(__file__).resolve().parents[2] / '.runtime' / 'stop-quick-fixture'
    stop.parent.mkdir(exist_ok=True)
    stop.unlink(missing_ok=True)
    ProfileDatabaseFixture.setUpClass()
    fixture = ProfileDatabaseFixture()
    fixture.setUp()
    seed(fixture)
    module.call_qwen = no_model
    module.MODEL = 'fixture-no-model'
    module.EMBEDDER = HashingEmbedder(dimensions=128)
    module.EMBEDDER.configured = lambda: True
    os.environ['QWEN_API_KEY'] = 'fixture-only'
    os.environ['QWEN_BASE_URL'] = 'https://fixture.invalid/v1'
    server = make_server('127.0.0.1', 5058, module.app, threaded=True)
    server.timeout = 1
    try:
        print('Isolated one-click lesson UI: http://127.0.0.1:5058; temporary schema=' + fixture.schema, flush=True)
        deadline = time.monotonic() + 1800
        while not stop.exists() and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
        fixture.tearDown()
        ProfileDatabaseFixture.tearDownClass()
        stop.unlink(missing_ok=True)
