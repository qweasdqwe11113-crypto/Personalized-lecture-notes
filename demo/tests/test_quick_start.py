"""The anchored material mode (the lesson's own material whole, retrieval only adding to it), knowledge-point
suggestions for a free-text focus, and the one-click lesson built on them (quick_start), in a disposable
schema. HashingEmbedder stands in for the embedding model; no generation service is called."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_profiles import ProfileDatabaseFixture
from test_retrieval import seed_university
import test_hybrid_retrieval as hybrid_case   # only its COURSE rows; its tests are not collected again here
import app as module
import embed
import prompts
import quality
import retrieval

COURSE_SCOPE = module.hybrid_scope      # the real scope, before the fixtures replace it
LESSON = (11, 12, 13)
# Teaching order: the lesson's chapter first; 16 (the 产线 Bayes example) and 14, 15 come in later chapters.
CHAPTERS = {11: ('第一章 条件概率', 1), 12: ('第一章 条件概率', 2), 13: ('第一章 条件概率', 3),
            15: ('第二章 随机变量', 4), 16: ('第二章 随机变量', 5), 14: ('第三章 蒙特卡洛', 6)}
NOTE = '该生做产线次品的应用题时，用贝叶斯公式求后验，总把 P(甲|D) 和 P(D|甲) 弄反'
FOCUS = '用全概率公式和贝叶斯公式求次品来自哪条产线的概率，并讨论先验变化对后验的影响'


class CourseFixture(ProfileDatabaseFixture):
    def setUp(self):
        super().setUp()
        seed_university(self)
        with self.fixture_db() as conn:
            for kid, name, content in hybrid_case.COURSE:
                conn.execute('INSERT INTO knowledge_points VALUES (%s,%s)', (kid, name))
                conn.execute('INSERT INTO materials VALUES (%s,%s,%s,%s,%s,%s,%s)',
                             (kid, kid, name, content, '隔离测试资料', f'https://example.invalid/{kid}', '自编'))
        self.embedder = embed.HashingEmbedder(dimensions=128)
        self.patches = [patch.object(module, 'EMBEDDER', self.embedder),
                        # The seeded lesson belongs to no course; the whole course is its points plus 14–16.
                        patch.object(module, 'hybrid_scope',
                                     lambda topic: list(dict.fromkeys([*topic['knowledge_ids'], 14, 15, 16])))]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        super().tearDown()

    def chapters(self, **changes):
        order = {**CHAPTERS, **{int(k): v for k, v in changes.items()}}
        with self.fixture_db() as conn:
            for kid, (chapter, position) in order.items():
                conn.execute('UPDATE knowledge_points SET chapter=%s, position=%s WHERE id=%s', (chapter, position, kid))

    def learner_limit(self, limit):
        patcher = patch.dict(retrieval.ROLES['learner'], limit=limit)
        patcher.start()
        self.addCleanup(patcher.stop)

    def more_material_outside(self):
        with self.fixture_db() as conn:
            for mid, content in ((20, '应用题：检测呈阳性时真正患病的概率。先用全概率公式求 P(阳性)，再用贝叶斯公式求后验；'
                                       'P(患病|阳性) 与 P(阳性|患病) 的方向不同，不能互换。'),
                                 (21, '应用题：三条产线的次品来源。用贝叶斯公式分别求次品来自各产线的后验概率，'
                                       '并检查三个后验之和为 1。')):
                conn.execute('INSERT INTO materials VALUES (%s,16,%s,%s,%s,%s,%s)',
                             (mid, f'应用题 {mid}', content, '隔离测试资料', f'https://example.invalid/{mid}', '自编'))


class AnchoredModeTests(CourseFixture):
    def context(self, **options):
        return module.get_context(options.pop('student', 11), 'probability_advanced', options.pop('mode', 'profile'),
                                  'anchored', **options)

    def test_the_lessons_material_is_given_whole_and_the_learner_adds_passages(self):
        self.learner_limit(4)
        exact = module.get_context(11, 'probability_advanced')
        ctx = self.context(learner_note=NOTE)
        found = ctx['retrieval']
        self.assertEqual((found['mode'], found['version'], found['base']['kind']), ('anchored', 'anchored-v1', 'materials'))
        whole = {m['id']: m['content'] for m in exact['materials']}
        given = {m['id']: m for m in ctx['materials']}
        for mid, content in whole.items():
            self.assertEqual(given[mid]['content'], content)            # the lookup's text, not an excerpt
            self.assertTrue(given[mid]['use'].startswith('本课知识点'))
        self.assertEqual(found['base']['characters'], sum(map(len, whole.values())))
        self.assertEqual([m['id'] for m in found['base']['materials']], sorted(whole))
        # Retrieval only adds: passages for the learner from outside the lesson, never the lesson's own chunks.
        self.assertTrue(found['items'])
        self.assertTrue(all(item['role'] == 'learner' and not item.get('anchor') and item['knowledge_id'] not in LESSON
                            for item in found['items']))
        self.assertIn(16, {item['material_id'] for item in found['items']})
        self.assertEqual(given[16]['use'], '针对学情')
        self.assertEqual(set(given) - set(whole), {item['material_id'] for item in found['items']})
        self.assertLessEqual(set(found['learner_in_lesson']), set(whole))
        self.assertEqual([q['role'] for q in found['queries']], ['learner'])
        self.assertEqual(self.context(learner_note=NOTE)['materials'], ctx['materials'])    # repeatable

    def test_without_a_learner_need_it_is_the_exact_lookup_labelled(self):
        exact = module.get_context(11, 'probability_advanced', 'none')
        ctx = self.context(mode='none')
        found = ctx['retrieval']
        self.assertEqual((found['backend'], found['ranked'], found['items'], found['queries']), ('lookup', False, [], []))
        self.assertEqual([(m['id'], m['content']) for m in ctx['materials']],
                         [(m['id'], m['content']) for m in exact['materials']])
        self.assertEqual({m['use'] for m in ctx['materials']}, {'本课知识点'})

    def test_later_chapters_never_supplement_the_lesson(self):
        self.learner_limit(4)
        self.chapters()
        found = self.context(learner_note=NOTE)['retrieval']
        self.assertEqual(found['boundary'], {'later_points': 3})
        self.assertEqual(found['scope']['knowledge_points'], 3)
        self.assertFalse({14, 15, 16} & {item['knowledge_id'] for item in found['items'] + found['alternatives']})
        # The same passage in an earlier chapter is what this learner needs.
        self.chapters(**{'16': ('预备知识', 0)})
        found = self.context(learner_note=NOTE)['retrieval']
        self.assertEqual(found['boundary'], {'later_points': 2})
        self.assertIn(16, {item['material_id'] for item in found['items']})

    def test_a_lesson_over_the_budget_is_cut_to_excerpts_that_fit(self):
        exact = module.get_context(11, 'probability_advanced', 'none')
        total = sum(len(m['content']) for m in exact['materials'])
        with patch.object(module, 'MATERIAL_BUDGET', 300):
            ctx = self.context(mode='none')
        found = ctx['retrieval']
        self.assertGreater(total, 300)
        self.assertEqual((found['base']['kind'], found['base']['characters']), ('chunks', total))
        self.assertLessEqual(sum(len(m['content']) for m in ctx['materials']), 300)
        self.assertTrue(found['items'])
        self.assertTrue(all(item.get('anchor') and item['knowledge_id'] in LESSON for item in found['items']))
        self.assertTrue(all(m['use'].startswith('本课知识点（节选）') for m in ctx['materials']))
        self.assertEqual({q['role'] for q in found['queries']}, {'explain', 'practice'})
        # What did not fit is offered back to the teacher.
        left = {m['id'] for m in exact['materials']} - {m['id'] for m in ctx['materials']}
        self.assertTrue(left)
        self.assertLessEqual(left, {item['material_id'] for item in found['alternatives'] if item.get('anchor')})
        with patch.object(module, 'MATERIAL_BUDGET', 300), self.assertRaises(retrieval.RetrievalError) as refused:
            self.context(mode='none', keep_chunks=[])
        self.assertEqual(refused.exception.code, 'empty_selection')

    def test_teacher_changes_touch_only_the_passages(self):
        self.more_material_outside()
        automatic = self.context(learner_note=NOTE)
        found = automatic['retrieval']
        self.assertTrue(found['items'] and found['alternatives'])
        # Nothing kept: the lesson's own material alone, whole.
        bare = self.context(learner_note=NOTE, keep_chunks=[])
        self.assertEqual((bare['retrieval']['selection'], bare['retrieval']['items']), ('teacher', []))
        self.assertEqual(sorted(bare['retrieval']['removed']), sorted(item['key'] for item in found['items']))
        self.assertEqual({m['id'] for m in bare['materials']}, set(LESSON))
        # A chunk of the lesson's own material changes nothing: it is given whole anyway.
        with self.fixture_db() as conn:
            own = retrieval.candidates(conn, [11])
        same = self.context(learner_note=NOTE, keep_chunks=[retrieval.kept_id(own[0])])
        self.assertEqual(({m['id'] for m in same['materials']}, same['retrieval']['items']), (set(LESSON), []))
        # A runner-up the teacher adds is kept as theirs.
        extra = found['alternatives'][0]
        chosen = self.context(learner_note=NOTE, keep_chunks=[retrieval.kept_id(extra)])
        self.assertEqual([(item['key'], item['role']) for item in chosen['retrieval']['items']], [(extra['key'], 'teacher')])
        # More than the budget is refused before anything is sent.
        fits = found['base']['characters'] + sum(item['char_length'] + retrieval.GAP for item in found['items'])
        with patch.object(module, 'MATERIAL_BUDGET', fits):
            self.assertEqual(self.context(learner_note=NOTE)['retrieval']['base']['kind'], 'materials')
            keep = [retrieval.kept_id(item) for item in found['items'] + found['alternatives']]
            with self.assertRaises(retrieval.RetrievalError) as refused:
                self.context(learner_note=NOTE, keep_chunks=keep)
        self.assertEqual(refused.exception.code, 'over_budget')

    def test_keep_lists_may_be_empty_only_where_material_remains(self):
        self.assertEqual(module.retrieval_keep({'retrieval_chunk_ids': []}, 'anchored'), [])
        for mode in ('hybrid', 'sql'):
            with self.subTest(mode=mode), self.assertRaises(module.JobError):
                module.retrieval_keep({'retrieval_chunk_ids': []}, mode)

    def test_the_model_gets_the_anchored_rules_and_not_the_record(self):
        ctx = self.context(learner_note=NOTE)
        prepared = prompts.prepare_request(ctx, ctx['topic']['goal'], 'fixture-model')
        system, user = (message['content'] for message in json.loads(prepared.body)['messages'])
        self.assertIn('【课程资料：本课知识点的全部资料，加上整门课的补充】', system)
        self.assertNotIn('【课程资料来自整门课的检索】', system)
        self.assertIn('+lecture-retrieval-anchored-v1', prepared.prompt_version)
        sent = json.loads(user)['context']
        self.assertNotIn('retrieval', sent)
        self.assertEqual([m['citation_id'] for m in sent['materials']], [f"M{m['id']}" for m in ctx['materials']])
        self.assertTrue(all(m['use'] for m in sent['materials']))

    def test_the_usage_check_counts_the_lessons_material_and_the_supplements(self):
        self.learner_limit(4)
        ctx = self.context(learner_note=NOTE)

        def check(text):
            return next(c for c in quality.check_output(text, ctx, ctx['topic']['goal'])['automatic']
                        if c['code'] == 'retrieval_usage')
        silent = check('# 标题\n## 一\n没有引用任何材料。')
        self.assertEqual(silent['status'], 'warning')
        self.assertIn('本课知识点的资料 3 份，正文引用了其中 0 份', silent['detail'])
        self.assertIn('请看讲义是否照顾到学情', silent['detail'])
        cited = check('# 标题\n## 一\n条件概率 [M11]，应用题 [M16]。')
        self.assertEqual(cited['status'], 'pass')
        self.assertIn('引用了其中 1 份', cited['detail'])
        self.assertNotIn('请看讲义是否照顾到学情', cited['detail'])
        self.assertEqual(retrieval.MODE_LABELS['anchored'], '本课知识点的全部资料 + 整门课补充')


class SuggestPointsTests(CourseFixture):
    def suggest(self, text, **options):
        self.chapters()
        with self.fixture_db() as conn:
            library = [dict(row) for row in conn.execute('''SELECT id, name, chapter, position FROM knowledge_points
                WHERE id = ANY(%s) ORDER BY position''', ([11, 12, 13, 14, 15, 16],))]
            retrieval.index_knowledge(conn, self.embedder, [point['id'] for point in library])
        with self.fixture_db() as conn:
            return retrieval.suggest_points(conn, options.pop('embedder', self.embedder), text, library=library, **options)

    def test_a_focus_ticks_the_points_whose_material_matches(self):
        found = self.suggest(FOCUS)
        self.assertEqual(found['version'], 'point-suggest-v1')
        self.assertIn(16, found['suggested'])
        self.assertTrue({12, 13} & set(found['suggested']))
        self.assertFalse({14, 15} & set(found['suggested']))
        first = next(p for p in found['points'] if p['knowledge_id'] == found['suggested'][0])
        self.assertEqual(first['share'], 1.0)
        self.assertTrue(first['evidence'] and first['evidence'][0]['excerpt'])
        self.assertTrue(all(p['suggested'] == (p['knowledge_id'] in found['suggested']) for p in found['points']))
        self.assertEqual((found['warnings'], found['scope']['without_material']), ([], 0))
        self.assertEqual(self.suggest(FOCUS)['suggested'], found['suggested'])              # repeatable

    def test_points_after_the_chosen_chapter_are_listed_but_not_ticked(self):
        found = self.suggest(FOCUS, chapter='第一章 条件概率')
        later = next(p for p in found['points'] if p['knowledge_id'] == 16)
        self.assertEqual((later['later'], later['suggested']), (True, False))
        self.assertNotIn(16, found['suggested'])
        self.assertTrue(any('之后的章节' in warning for warning in found['warnings']))

    def test_a_short_or_vague_focus_is_warned_about(self):
        short = self.suggest('概率')
        self.assertTrue(any('太短' in warning for warning in short['warnings']))
        with patch.object(retrieval, 'SUGGEST_MAX', 1):
            vague = self.suggest(FOCUS)
        self.assertEqual(len(vague['suggested']), 1)
        self.assertTrue(any('笼统' in warning for warning in vague['warnings']))

    def test_without_vectors_the_points_are_found_by_keywords(self):
        found = self.suggest('产线次品的贝叶斯应用题，先用全概率公式', embedder=None, vector_note='嵌入模型尚未配置。')
        self.assertEqual((found['backend'], found['vector']['used']), ('bm25', False))
        self.assertIn(16, found['suggested'])


class QuickLessonApiTests(CourseFixture):
    def setUp(self):
        super().setUp()
        with self.fixture_db() as conn:
            self.course = conn.execute("INSERT INTO courses(name) VALUES ('一键备课测试') RETURNING id").fetchone()['id']
            self.klass = conn.execute("INSERT INTO classes(course_id,name) VALUES (%s,'一班') RETURNING id",
                                      (self.course,)).fetchone()['id']
            conn.execute('INSERT INTO class_students(class_id,course_id,student_id) VALUES (%s,%s,11)', (self.klass, self.course))
            conn.execute('UPDATE knowledge_points SET course_id=%s WHERE id = ANY(%s)', (self.course, [11, 12, 13, 14, 15, 16]))
        self.chapters()

    def post(self, url, **data):
        return self.client.post(url, json={'course_id': self.course, 'class_id': self.klass, **data})

    def test_the_library_lists_the_course_in_teaching_order(self):
        response = self.client.get(f'/api/quick-lesson/library?course_id={self.course}&class_id={self.klass}')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([p['id'] for p in response.json['points']], [11, 12, 13, 15, 16, 14])
        self.assertEqual(response.json['chapters'], ['第一章 条件概率', '第二章 随机变量', '第三章 蒙特卡洛'])
        self.assertTrue(all(p['materials'] == 1 for p in response.json['points']))

    def test_a_suggested_lesson_is_created_and_generates_like_any_other(self):
        text = '小许总把产线次品题里的 P(甲|D) 和 P(D|甲) 弄反：用全概率公式和贝叶斯公式求次品来自哪条产线'
        with patch.object(self.embedder, 'embed', wraps=self.embedder.embed) as calls:
            response = self.post('/api/quick-lesson/suggest', text=text)
        self.assertEqual(response.status_code, 200, response.json)
        found = response.json['suggestion']
        self.assertIn(16, found['suggested'])
        sent = ' '.join(text for call in calls.call_args_list for text in call.args[0])
        self.assertNotIn('小许', sent + found['text'])                  # the student's name never leaves
        self.assertIn('该生', found['text'])
        # The teacher keeps the worked example, drops the other suggestions and adds conditional probability.
        ids = [16, 11] if 11 not in found['suggested'] else [16]
        created = self.post('/api/quick-lesson', text=text, knowledge_ids=ids, suggested_ids=found['suggested'])
        self.assertEqual(created.status_code, 201, created.json)
        task, key = created.json['task'], created.json['key']
        self.assertEqual(task['knowledge_ids'], sorted(ids, key=[11, 12, 13, 15, 16, 14].index))   # teaching order
        self.assertEqual((task['goal'], task['course_id'], task['chapter']), (text, self.course, '第一章 条件概率'))
        record = task['quick_start']
        self.assertEqual((record['version'], record['suggested'], record['confirmed']),
                         ('point-suggest-v1', found['suggested'], task['knowledge_ids']))
        self.assertEqual(record['added'], [k for k in task['knowledge_ids'] if k not in found['suggested']])
        self.assertEqual(record['dropped'], [k for k in found['suggested'] if k not in ids])
        boot = self.client.get('/api/bootstrap').json['topics'][key]
        self.assertEqual((boot['knowledge_ids'], boot['course_id']), (task['knowledge_ids'], self.course))
        with patch.object(module, 'hybrid_scope', COURSE_SCOPE):       # the lesson's course, as in use
            ctx = module.get_context(11, key, 'profile', 'anchored')
        self.assertTrue(set(ids) <= {m['id'] for m in ctx['materials']})
        self.assertEqual(ctx['retrieval']['base']['knowledge_ids'], task['knowledge_ids'])
        self.assertEqual(ctx['retrieval']['scope']['knowledge_points'], 6 - ctx['retrieval']['boundary']['later_points'])

    def test_bad_requests_are_refused_with_reasons(self):
        cases = [('/api/quick-lesson/suggest', dict(text='短'), 400),
                 ('/api/quick-lesson/suggest', dict(text='条件概率的方向', chapter='第九章'), 400),
                 ('/api/quick-lesson/suggest', dict(text='条件概率的方向', course_id='1'), 400),
                 ('/api/quick-lesson/suggest', dict(text='条件概率的方向', class_id=999999), 400),
                 ('/api/quick-lesson/suggest', dict(text='条件概率的方向', course_id=999999), 404),
                 ('/api/quick-lesson', dict(text='条件概率的方向', knowledge_ids=[]), 400),
                 ('/api/quick-lesson', dict(text='条件概率的方向', knowledge_ids=[11, 11]), 400),
                 ('/api/quick-lesson', dict(text='条件概率的方向', knowledge_ids=[11], suggested_ids='11'), 400),
                 ('/api/quick-lesson', dict(text='条件概率的方向', knowledge_ids=[999]), 409),
                 ('/api/quick-lesson', dict(text='条件概率的方向', knowledge_ids=[11], title='长' * 121), 400)]
        for url, data, status in cases:
            with self.subTest(url=url, data=data):
                self.assertEqual(self.post(url, **data).status_code, status)
        with self.fixture_db() as conn:
            empty = conn.execute("INSERT INTO courses(name) VALUES ('空课程') RETURNING id").fetchone()['id']
        response = self.client.post('/api/quick-lesson/suggest', json={'course_id': empty, 'text': '条件概率的方向'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('知识点库还是空的', response.json['error'])


if __name__ == '__main__':
    unittest.main()
