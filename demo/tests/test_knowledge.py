"""Course knowledge library and recognition contracts; the model and the upload store are fixtures."""
import json
from threading import Lock
from unittest.mock import patch

from test_profiles import ProfileDatabaseFixture, module
from classes import ensure_course_structure, library_ids
import knowledge
from knowledge import KnowledgeQueue, blocks, parse_points, strip_furniture, structure

NOTES = '\n'.join([
    '第二章 随机变量与分布', '',
    '2.1.1 Definition of Random Variable', '随机变量是定义在样本空间上的实值函数。' * 12, '',
    '2.1.2 Description of Probability Distribution', '分布律、概率密度与分布函数描述随机变量的取值规律。' * 12, '',
    '2.2 Common Discrete Distributions', '伯努利分布、二项分布与泊松分布；泊松定理给出二项分布的近似。' * 12,
])
UPLOAD = {'id': 'a' * 32, 'title': '第二章讲义', 'filename': 'chapter2.md', 'source_url': '', 'content': NOTES,
          'kind': 'file', 'raw': NOTES.encode()}


class RuleTests(ProfileDatabaseFixture):
    def test_pages_keep_headings_drop_running_footers_and_skip_contents_pages(self):
        pages = [('第 1 页', '第二章随机变量与分布\nChapter 2'),
                 ('第 2 页', 'Chapter 2  Random Variables\n• 2.1 Introduction\n• 2.1.1 Definition\n• 2.2 Discrete\nChapter 2')]
        pages += [(f'第 {n} 页', f'{head}\nChapter 2  Page {n}\n正文 {n}') for n, head in
                  enumerate(['2.1 Introduction', '2.1.1 Definition of Random Variable', '', '0.667 0.083 0.250',
                             '2.2 Common Discrete Distributions', '2.1 Introduction'], 3)]
        cleaned = strip_furniture(pages)
        self.assertNotIn('Page', ''.join(text for _, text in cleaned))
        self.assertIn('2.2 Common Discrete Distributions', cleaned[6][1])
        chapter, sections, toc = structure(cleaned)
        self.assertEqual(chapter, '第二章 随机变量与分布')
        self.assertEqual(toc, [2])
        # Pages without a heading (and a row of numbers) continue the section; a repeated number extends it.
        self.assertEqual([(s['number'], s['units']) for s in sections],
                         [('2.1', [3, 8]), ('2.1.1', [4, 6]), ('2.2', [7, 7])])

    def test_a_short_text_keeps_every_section_it_holds(self):
        # The whole file is one block; each heading in it is still its own section, with its number.
        text = '\n\n'.join(['# 第七章 大数定律与中心极限定理', '## 7.1 切比雪夫不等式', '偏离概率的上界。',
                             '## 7.2 大数定律', '样本均值依概率收敛。', '## 7.3 中心极限定理', '标准化的和近似正态。', '## 小结'])
        units = blocks(text)
        self.assertEqual(len(units), 1)
        chapter, sections, _ = structure(units)
        self.assertEqual(chapter, '第七章 大数定律与中心极限定理')
        self.assertEqual([(s['number'], s['title'], s['units']) for s in sections],
                         [('7.1', '切比雪夫不等式', [1, 1]), ('7.2', '大数定律', [1, 1]),
                          ('7.3', '中心极限定理', [1, 1]), ('', '小结', [1, 1])])
        # Markdown headings are the author's own, so a number used once is not taken for a stray value.
        _, sections, _ = structure(blocks('## 1.1 计数原理\n\n## 2.1 随机事件\n\n## 3.1 古典概型'))
        self.assertEqual([s['number'] for s in sections], ['1.1', '2.1', '3.1'])

    def test_model_points_are_clamped_merged_and_invalid_ones_dropped(self):
        chapter, items = parse_points('```json\n' + json.dumps({'chapter': '第二章', 'points': [
            {'name': '随机变量', 'units': [0, 2], 'summary': 's'}, {'name': '随机变量', 'units': [3, 3]},
            {'name': '', 'units': [1, 1]}, {'name': '越界', 'units': [9, 12]}, {'name': '单页', 'units': 4}]},
            ensure_ascii=False) + '\n```', 5)
        self.assertEqual(chapter, '第二章')
        self.assertEqual([(i['name'], i['units']) for i in items], [('随机变量', [1, 3]), ('单页', [4, 4])])
        with self.assertRaises(module.ModelError):
            parse_points('不是 JSON', 5)
        with self.assertRaises(module.ModelError):
            parse_points('{"points": [{"name": "x"}]}', 5)


class LibraryTests(ProfileDatabaseFixture):
    def setUp(self):
        super().setUp()
        with self.fixture_db() as conn:
            ensure_course_structure(conn)
            self.course = conn.execute("SELECT id FROM courses WHERE preset_key='junior-math'").fetchone()['id']
        self.upload = {**UPLOAD, 'course_id': self.course, 'class_id': None}
        self.reader = patch.object(knowledge, 'read_upload', lambda root, uid, raw=False: dict(self.upload) if uid == self.upload['id'] else None)
        self.reader.start()
        self.calls = []
        self.reply = None
        self.queue = KnowledgeQueue(lambda: module.db(), module.ROOT, self.model, Lock(), lambda: 'fixture-model')
        self.queue_patch = patch.object(module, 'KNOWLEDGE', self.queue)
        self.queue_patch.start()
        self.offline = patch.object(module, 'call_qwen', side_effect=module.ModelError('test', '隔离测试，不调用模型。'))
        self.offline.start()

    def tearDown(self):
        self.queue.wait_idle(5)
        self.queue_patch.stop()
        self.reader.stop()
        self.offline.stop()

    def model(self, prepared, on_attempt):
        on_attempt()
        self.calls.append(prepared)
        if self.reply is None:
            raise module.ModelError('offline', '模型服务不可用')
        return module.ModelResult(json.dumps(self.reply, ensure_ascii=False))

    def json(self, method, url, body=None, status=200):
        response = getattr(self.client, method)(url, json=body)
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def recognize(self):
        self.json('post', '/api/knowledge/recognize', {'upload_id': self.upload['id']}, 202)
        self.assertTrue(self.queue.wait_idle(10))
        return self.json('get', f"/api/knowledge/drafts/{self.upload['id']}")

    def test_backfill_files_lesson_points_under_their_course(self):
        with self.fixture_db() as conn:
            rows = conn.execute('SELECT id,course_id,chapter FROM knowledge_points ORDER BY id').fetchall()
            self.assertEqual({(r['course_id'], r['chapter']) for r in rows}, {(self.course, '等式与一元一次方程')})
            self.assertEqual(library_ids(conn, self.course), [1, 2, 3])

    def test_model_draft_is_reviewed_then_confirmed_into_the_library_with_source_segments(self):
        self.reply = {'chapter': '第二章 随机变量与分布', 'points': [
            {'name': '随机变量的定义', 'section': '2.1.1', 'units': [1, 2], 'summary': '样本空间上的实值函数'},
            {'name': '泊松分布', 'section': '2.2', 'units': [3, 3], 'summary': '泊松定理'}]}
        detail = self.recognize()
        draft = detail['draft']
        self.assertEqual((draft['status'], draft['method']), ('ready', 'rules+model'))
        self.assertEqual([i['name'] for i in draft['items']], ['随机变量的定义', '泊松分布'])
        body = json.loads(self.calls[0].body)
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        sent = json.loads(body['messages'][1]['content'])
        self.assertEqual([s['number'] for s in sent['sections']], ['2.1.1', '2.1.2', '2.2'])
        # The model sees the library's names so a segment about an existing point reuses its name.
        with self.fixture_db() as conn:
            shelf = [r['name'] for r in conn.execute('SELECT name FROM knowledge_points WHERE id=ANY(%s) ORDER BY position,id',
                                                     (library_ids(conn, self.course),))]
        self.assertEqual(sent['library'], shelf)
        self.assertEqual(self.calls[0].prompt_version, 'knowledge-recognition-v2')
        self.assertIn('逐字使用 library 中的名称', body['messages'][0]['content'])
        self.assertTrue(all(u['label'].endswith('段') for u in detail['units']))
        # The teacher renames one point, adds one whose name the library already has, and keeps the other.
        items = [{**draft['items'][0], 'name': '随机变量'}, draft['items'][1],
                 {'name': '两步方程与移项', 'units': [2, 2], 'chapter': '第二章 随机变量与分布'}]
        stale = self.client.post(f"/api/knowledge/drafts/{self.upload['id']}/confirm", json={'items': items, 'version': 'old'})
        self.assertEqual(stale.status_code, 409)
        duplicate = self.client.post(f"/api/knowledge/drafts/{self.upload['id']}/confirm",
                                     json={'items': items + [items[0]], 'version': draft['version']})
        self.assertEqual(duplicate.status_code, 400)
        result = self.json('post', f"/api/knowledge/drafts/{self.upload['id']}/confirm", {'items': items, 'version': draft['version']})
        self.assertEqual((result['created'], result['reused']), (2, 1))
        self.assertEqual(result['items'][2]['knowledge_id'], 3)
        library = self.json('get', f'/api/courses/{self.course}/knowledge')
        names = [p['name'] for p in library['points']]
        self.assertEqual(names[-2:], ['随机变量', '泊松分布'])
        point = library['points'][-1]
        self.assertEqual(point['chapter'], '第二章 随机变量与分布')
        self.assertEqual(point['sources'][0]['source_url'], f"upload:{self.upload['id']}#s3-3")
        self.assertIn('第 3 段', point['sources'][0]['section'])
        self.assertEqual(library['drafts'][0]['status'], 'confirmed')
        with self.fixture_db() as conn:
            content = conn.execute('SELECT content FROM materials WHERE knowledge_id=%s', (point['id'],)).fetchone()['content']
        self.assertTrue(content.startswith('【第 3 段】'))
        self.assertIn('泊松定理', content)
        # Confirming again refreshes the same segments instead of duplicating them.
        again = self.json('get', f"/api/knowledge/drafts/{self.upload['id']}")['draft']
        self.json('post', f"/api/knowledge/drafts/{self.upload['id']}/confirm", {'items': items, 'version': again['version']})
        self.assertEqual(len(self.json('get', f'/api/courses/{self.course}/knowledge')['points'][-1]['sources']), 1)

    def test_model_failure_falls_back_to_section_headings_and_unscoped_uploads_are_refused(self):
        draft = self.recognize()['draft']
        self.assertEqual(draft['method'], 'rules')
        self.assertIn('模型识别未完成', draft['notice'])
        self.assertEqual([i['name'] for i in draft['items']],
                         ['Definition of Random Variable', 'Description of Probability Distribution', 'Common Discrete Distributions'])
        self.assertEqual(draft['items'][0]['chapter'], '第二章 随机变量与分布')
        self.upload['course_id'] = None
        refused = self.client.post('/api/knowledge/recognize', json={'upload_id': self.upload['id']})
        self.assertEqual(refused.status_code, 400)

    def test_lessons_tick_library_points_share_their_materials_and_follow_renames(self):
        point = self.json('post', f'/api/courses/{self.course}/knowledge', {'name': '泊松分布', 'chapter': '第二章'}, 201)['point']
        self.assertEqual(self.client.post(f'/api/courses/{self.course}/knowledge', json={'name': '泊松分布'}).status_code, 409)
        with self.fixture_db() as conn:
            conn.execute('''INSERT INTO materials(id,knowledge_id,title,content,source_title,source_url,section)
                VALUES (900,%s,'泊松分布 · 讲义','泊松定理：n 大 p 小时二项分布近似泊松分布。','ch2.pdf','upload:x#p3-4','')''', (point['id'],))
        lesson = {'course_id': self.course, 'chapter': '第二章', 'title': '泊松分布', 'goal': '理解泊松定理',
                  'task_level': '基础任务', 'student_ids': [1], 'knowledge_ids': [point['id'], 2],
                  'knowledge_names': ['泊松分布', '等式的加减性质', '泊松近似的条件']}
        saved = self.json('post', '/api/course-tasks', lesson, 201)['task']
        self.assertEqual(saved['knowledge_ids'][:3], [point['id'], 2, 1])
        new_point = saved['knowledge_ids'][3]
        self.assertEqual(saved['knowledge_names'][3], '泊松近似的条件')
        with self.fixture_db() as conn:
            self.assertIn(new_point, library_ids(conn, self.course))
        context = module.get_context(1, next(k for k, t in module.topic_catalog().items() if t.get('title') == '泊松分布'))
        self.assertIn('M900', [f"M{m['id']}" for m in context['materials']])
        refused = self.client.post('/api/course-tasks', json={**lesson, 'knowledge_ids': [999]})
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(self.client.post('/api/course-tasks', json={**lesson, 'knowledge_ids': [], 'knowledge_names': []}).status_code, 400)
        # Renaming a library point updates the lesson's copy of the name; archiving hides it from the picker only.
        key = next(k for k, t in module.topic_catalog().items() if t.get('title') == '泊松分布')
        self.json('put', f"/api/knowledge/{point['id']}", {'name': '泊松分布（Poisson）'})
        self.assertEqual(module.topic_catalog()[key]['knowledge_names'][0], '泊松分布（Poisson）')
        self.json('put', f"/api/knowledge/{point['id']}", {'archived': True})
        with self.fixture_db() as conn:
            self.assertNotIn(point['id'], library_ids(conn, self.course))
        self.assertIn(point['id'], module.topic_catalog()[key]['knowledge_ids'])
        # Typing the archived name again brings the same point back instead of a duplicate.
        again = self.json('post', '/api/course-tasks', {**lesson, 'title': '复习', 'knowledge_ids': [],
                                                         'knowledge_names': ['泊松分布（Poisson）']}, 201)['task']
        self.assertEqual(again['knowledge_ids'], [point['id']])
        self.assertEqual(self.client.put('/api/knowledge/1', json={'name': '泊松近似的条件'}).status_code, 409)

    def test_names_differing_only_in_width_spacing_or_case_name_one_library_point(self):
        point = self.json('post', f'/api/courses/{self.course}/knowledge', {'name': '泊松分布（Poisson）'}, 201)['point']
        clash = self.json('post', f'/api/courses/{self.course}/knowledge', {'name': '泊松分布 (poisson)'}, status=409)
        self.assertIn('「泊松分布（Poisson）」', clash['error'])
        other = self.json('post', f'/api/courses/{self.course}/knowledge', {'name': '二项分布'}, 201)['point']
        self.json('put', f"/api/knowledge/{other['id']}", {'name': '泊松分布(POISSON)'}, status=409)
        self.json('put', f"/api/knowledge/{point['id']}", {'archived': True})
        self.assertIn('已归档', self.json('post', f'/api/courses/{self.course}/knowledge', {'name': '泊松分布(Poisson)'}, status=409)['error'])
        # A lesson typing the name another way reuses the point (restoring it) under the library spelling.
        lesson = {'course_id': self.course, 'chapter': '第二章', 'title': '泊松分布', 'goal': '理解泊松定理',
                  'task_level': '基础任务', 'student_ids': [1], 'knowledge_ids': [], 'knowledge_names': ['泊松分布(poisson)']}
        self.json('post', '/api/course-tasks', {**lesson, 'knowledge_names': ['A（B）', 'a(b)']}, status=400)
        created = self.json('post', '/api/course-tasks', lesson, 201)
        self.assertEqual((created['task']['knowledge_ids'], created['task']['knowledge_names']), ([point['id']], ['泊松分布（Poisson）']))
        # Editing the lesson and naming a new point keeps the lesson's own key.
        edited = self.json('put', f"/api/course-tasks/{created['key']}",
                           {**lesson, 'revision': 0, 'knowledge_ids': [point['id']], 'knowledge_names': ['泊松近似的条件']})
        self.assertEqual((edited['key'], edited['task']['revision']), (created['key'], 1))
        self.assertEqual([k for k, t in module.topic_catalog().items() if t.get('title') == '泊松分布'], [created['key']])
        # Recognition confirms 「泊松分布(POISSON)」 into the same point and files its segment under the library name.
        self.reply = {'chapter': '第二章 随机变量与分布', 'points': [
            {'name': '泊松分布(POISSON)', 'section': '2.2', 'units': [3, 3], 'summary': '泊松定理'}]}
        detail = self.recognize()
        self.assertIn('泊松分布（Poisson）', detail['library'])
        result = self.json('post', f"/api/knowledge/drafts/{self.upload['id']}/confirm",
                           {'items': detail['draft']['items'], 'version': detail['draft']['version']})
        self.assertEqual((result['created'], result['items'][0]['knowledge_id'], result['items'][0]['name']),
                         (0, point['id'], '泊松分布（Poisson）'))
        with self.fixture_db() as conn:
            title = conn.execute("SELECT title FROM materials WHERE knowledge_id=%s AND source_url LIKE 'upload:%%'",
                                 (point['id'],)).fetchone()['title']
        self.assertTrue(title.startswith('泊松分布（Poisson） · '))

    def test_student_record_in_a_course_lists_library_points(self):
        point = self.json('post', f'/api/courses/{self.course}/knowledge', {'name': '泊松分布'}, 201)['point']
        states = self.json('get', f'/api/students/1/profile?course_id={self.course}')['states']
        self.assertIn(point['id'], [s['id'] for s in states])
        self.assertIn(1, [s['id'] for s in states])

    def test_library_keeps_chapter_order_whatever_the_confirmation_order(self):
        for name, chapter in (('联合分布', '第三章 联合分布'), ('散记', ''), ('泊松分布', '第二章 随机变量与分布'),
                              ('卡方检验', '第十一章 检验')):
            self.json('post', f'/api/courses/{self.course}/knowledge', {'name': name, 'chapter': chapter}, 201)
        names = [p['name'] for p in self.json('get', f'/api/courses/{self.course}/knowledge')['points']]
        self.assertEqual(names, ['等式的加减性质', '等式的乘除性质', '两步方程与移项', '泊松分布', '联合分布', '卡方检验', '散记'])
        with self.fixture_db() as conn:
            self.assertEqual(library_ids(conn, self.course)[3:5], [p for p in [
                conn.execute("SELECT id FROM knowledge_points WHERE name=%s", (n,)).fetchone()['id'] for n in ('泊松分布', '联合分布')]])
