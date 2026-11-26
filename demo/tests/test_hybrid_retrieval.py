"""Whole-course (hybrid) retrieval in a disposable schema. No embedding or generation service is called:
HashingEmbedder stands in for the embedding model, and the ranking is checked for its properties."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_profiles import ProfileDatabaseFixture
from test_retrieval import seed_university
import app as module
import designed
import embed
import prompts
import quality
import retrieval

# Three more points of the same course, outside the lesson's own (11–13).
COURSE = [
    (14, '蒙特卡洛方法', '## 4.1 蒙特卡洛方法\n定义：用随机抽样得到的频率近似概率或积分，这种方法称为蒙特卡洛方法。样本越多，近似通常越稳定。\n'
                    '例1 在单位正方形内随机投点，求落在四分之一圆内的频率，并据此估计圆周率 π。\n'
                    '解：投点 n 次，记落入四分之一圆内的次数为 m，则 π ≈ 4m/n。投点越多，估计的波动越小。'),
    (15, '数学期望', '## 2.3 数学期望\n定义：离散型随机变量 X 的数学期望定义为 E(X)=Σ x·P(X=x)，它是取值按概率加权的平均。\n'
                 '例2 掷一枚均匀骰子，求点数的期望。\n解：E(X)=(1+2+3+4+5+6)/6=3.5。'),
    (16, '概率应用题', '## 1.5 全概率与贝叶斯的应用题\n'
                  '例3 甲、乙两条产线的产量占比为 0.6 和 0.4，次品率为 0.02 和 0.05。抽到一件次品，求它来自甲产线的概率。\n'
                  '解：先用全概率公式求 P(D)=0.6×0.02+0.4×0.05=0.032，再用贝叶斯公式得 P(甲|D)=0.012/0.032=0.375。\n'
                  '练习 2 若甲产线占比改为 0.8，后验概率怎样变化？先猜方向，再计算并检查后验之和为 1。'),
]


class HybridRetrievalTests(ProfileDatabaseFixture):
    def setUp(self):
        super().setUp()
        seed_university(self)
        with self.fixture_db() as conn:
            for kid, name, content in COURSE:
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

    def context(self, **options):
        return module.get_context(options.pop('student', 11), 'probability_advanced', options.pop('mode', 'profile'),
                                  'hybrid', **options)

    def keys(self, ctx):
        return [item['key'] for item in ctx['retrieval']['items']]

    def more_related_material(self):
        """More related material than the roles take, so there are runners-up."""
        with self.fixture_db() as conn:
            for mid, kid, content in ((17, 12, '全概率公式：若 A1、A2 构成互斥完备划分，则 P(D)=P(A1)P(D|A1)+P(A2)P(D|A2)。'),
                                      (18, 13, '贝叶斯公式：后验 P(A1|D) 等于先验乘似然再除以 P(D)，先验变化会改变后验。'),
                                      (19, 11, '条件概率：P(A|B)=P(A∩B)/P(B)，要求 P(B)>0；方向不同的条件概率一般不相等。')):
                conn.execute('INSERT INTO materials VALUES (%s,%s,%s,%s,%s,%s,%s)',
                             (mid, kid, f'补充材料 {mid}', content, '隔离测试资料', f'https://example.invalid/{mid}', '自编'))

    # ------------------------------------------------------------------ pure parts
    def test_a_worked_example_stays_with_its_solution(self):
        definition = '定义：' + '条件概率描述已知事件发生后另一事件发生的可能，分母必须为正。' * 9
        example = ('例1 两条产线的产量占比为 0.6、0.4，次品率为 0.02、0.05。' + '说明各数据的含义。' * 12
                   + '\n解：P(D)=0.6×0.02+0.4×0.05=0.032，P(A1|D)=0.012/0.032=0.375。')
        pieces = retrieval.describe(f'## 1.2 条件概率\n{definition}\n{example}\n练习 1 改变先验后重算后验。')
        holding = [piece for piece in pieces if '例1' in piece['content']]
        self.assertEqual(len(holding), 1)
        self.assertIn('解：', holding[0]['content'])              # the solution was not cut off from its problem
        self.assertIn('example', holding[0]['kind'])
        self.assertTrue(all(piece['heading'] == '## 1.2 条件概率' for piece in pieces))
        self.assertIn('definition', pieces[0]['kind'])
        self.assertEqual(retrieval.describe('第一行\n第二行'), retrieval.describe('第一行\n第二行'))

    def test_terms_and_ranking_are_plain_and_repeatable(self):
        self.assertEqual(retrieval.terms('⽅差 Variance of X'), ['方差', 'variance', 'of'])   # NFKC folds the radical form
        scores = retrieval.BM25(['条件概率的定义', '蒙特卡洛 投点 估计', '贝叶斯 后验']).scores('投点估计')
        self.assertEqual(retrieval.ranking(dict(enumerate(scores)), positive=True), [1])
        fused = retrieval.rrf([0, 1, 2], [2, 0, 1])
        self.assertEqual(max(fused, key=fused.get), 0)              # first and second beats third and first
        self.assertAlmostEqual(fused[0], 1 / 61 + 1 / 62)
        weighted = retrieval.rrf([0, 1], [1, 0], weights=[1.0, retrieval.KEYWORD_WEIGHT])
        self.assertEqual(max(weighted, key=weighted.get), 0)        # the vector ranking leads; BM25 counts half

    def test_vectors_of_an_older_index_format_are_rebuilt_once_with_the_title(self):
        with self.fixture_db() as conn:
            retrieval.index_knowledge(conn, self.embedder, [11])
        with self.fixture_db() as conn:
            conn.execute("UPDATE material_chunks SET index_format = 'text-v1'")
        with patch.object(self.embedder, 'embed', wraps=self.embedder.embed) as calls, self.fixture_db() as conn:
            retrieval.index_knowledge(conn, self.embedder, [11])
            retrieval.index_knowledge(conn, self.embedder, [11])
        self.assertEqual(calls.call_count, 1)
        self.assertTrue(all(text.startswith('条件概率与乘法公式\n') for text in calls.call_args.args[0]))
        with self.fixture_db() as conn:
            formats = {row['index_format'] for row in conn.execute('SELECT index_format FROM material_chunks')}
        self.assertEqual(formats, {retrieval.INDEX_FORMAT})

    def test_queries_have_purposes_and_only_personal_runs_read_the_learner(self):
        states = [{'name': '条件概率', 'state': 'needs_support', 'recent_evidence': [{'performance': '把分母写成 P(A)'}]},
                  {'name': '全概率', 'state': 'mastered', 'recent_evidence': [{'performance': '全对'}]}]
        plan = retrieval.plan_queries('推导贝叶斯公式', ['条件概率', '全概率'], states, '上次漏写正分母条件')
        self.assertEqual([query['role'] for query in plan], ['explain', 'practice', 'learner'])
        learner = plan[2]['text']
        self.assertIn('把分母写成 P(A)', learner)
        self.assertIn('上次漏写正分母条件', learner)
        self.assertNotIn('全对', learner)                             # a mastered point is not a learner need
        shared = retrieval.plan_queries('推导贝叶斯公式', ['条件概率', '全概率'], states, '备注', personal=False)
        self.assertEqual([query['role'] for query in shared], ['explain', 'practice'])

    # ------------------------------------------------------------------ the whole course
    def test_the_whole_course_is_searched_and_every_selected_point_keeps_material(self):
        ctx = self.context()
        found = ctx['retrieval']
        self.assertEqual(found['mode'], 'hybrid')
        self.assertEqual(found['backend'], 'bm25+array-cosine-rrf')
        self.assertTrue(found['vector']['used'])
        self.assertEqual(found['scope'], {'knowledge_points': 6, 'materials': 6, 'chunks': 6})
        keys = self.keys(ctx)
        self.assertEqual(len(keys), len(set(keys)))
        self.assertLessEqual(sum(item['role'] == 'explain' for item in found['items']), found['limits']['explain'])
        self.assertTrue({11, 12, 13} <= {item['knowledge_id'] for item in found['items']})
        # The worked Bayes example of another chapter is found, marked as outside the lesson, with both uses.
        outside = next(item for item in found['items'] if item['material_id'] == 16)
        self.assertFalse(outside['selected_point'])
        by_id = {row['id']: row for row in ctx['materials']}
        self.assertIn('例题练习', by_id[16]['use'])
        # Expectation and Monte Carlo share little more than the word 概率; no role is filled with them.
        self.assertFalse({14, 15} & set(by_id))
        self.assertEqual({row['id'] for row in ctx['materials']}, {item['material_id'] for item in found['items']})
        self.assertEqual(self.keys(self.context()), keys)            # repeatable

    def test_a_selected_point_the_queries_miss_still_keeps_its_best_chunk(self):
        with patch.object(module, 'plan_queries', lambda *args, **kwargs: [
                dict(role='explain', label='讲解依据', text='蒙特卡洛 投点 估计圆周率')]):
            found = self.context()['retrieval']
        floor = [item for item in found['items'] if item['coverage']]
        self.assertEqual({item['knowledge_id'] for item in floor}, {11, 12, 13})
        self.assertTrue(all(item['role'] == 'explain' for item in floor))
        self.assertIn(14, {item['knowledge_id'] for item in found['items'] if not item['coverage']})

    def test_without_an_answering_embedding_service_the_ranking_is_bm25_and_says_why(self):
        failure = embed.EmbeddingError('unreachable', '嵌入服务暂时无法连接。')
        with patch.object(self.embedder, 'embed', side_effect=failure):
            found = self.context()['retrieval']
        self.assertEqual(found['backend'], 'bm25')
        self.assertFalse(found['vector']['used'])
        self.assertIn('嵌入服务暂时无法连接', found['vector']['note'])
        self.assertTrue({11, 12, 13} <= {item['knowledge_id'] for item in found['items']})
        self.embedder.configured = lambda: False
        found = self.context()['retrieval']
        self.assertEqual(found['vector']['note'], '嵌入模型尚未配置，本次只按关键词排序。')
        with self.fixture_db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM material_chunks').fetchone()['n'], 0)

    def test_queries_sent_for_embedding_carry_no_student_name(self):
        with patch.object(self.embedder, 'embed', wraps=self.embedder.embed) as calls:
            found = self.context(learner_note='小许上次把条件概率的分母写成 P(A)')['retrieval']
        learner = next(query for query in found['queries'] if query['role'] == 'learner')
        self.assertIn('该生上次把条件概率的分母写成', learner['text'])
        sent = ' '.join(text for call in calls.call_args_list for text in call.args[0])
        self.assertNotIn('小许', sent)
        self.assertNotIn('小许', json.dumps(found['queries'], ensure_ascii=False))
        # Without a profile every student gets the same queries and chunks, so the comparison stays clean.
        plain = [self.context(student=student, mode='none')['retrieval'] for student in (11, 12)]
        self.assertEqual(plain[0]['queries'], plain[1]['queries'])
        self.assertEqual([i['key'] for i in plain[0]['items']], [i['key'] for i in plain[1]['items']])
        self.assertNotIn('learner', {query['role'] for query in plain[0]['queries']})

    def test_teacher_choice_is_exact_and_a_stale_choice_is_refused(self):
        self.more_related_material()
        for role, limit in (('explain', 2), ('practice', 1), ('learner', 1)):
            limits = patch.dict(retrieval.ROLES[role], limit=limit)
            limits.start()
            self.addCleanup(limits.stop)
        automatic = self.context()
        keys = self.keys(automatic)
        alternatives = automatic['retrieval']['alternatives']
        self.assertTrue(alternatives)
        self.assertFalse({item['key'] for item in alternatives} & set(keys))
        offered = automatic['retrieval']['items'][1:] + alternatives[:1]
        chosen = self.context(keep_chunks=[retrieval.kept_id(item) for item in offered])
        found = chosen['retrieval']
        self.assertEqual(found['selection'], 'teacher')
        self.assertEqual(found['removed'], keys[:1])
        self.assertEqual(set(self.keys(chosen)), {item['key'] for item in offered})
        added = next(item for item in found['items'] if item['key'] == alternatives[0]['key'])
        self.assertEqual((added['role'], added['role_label']), ('teacher', '教师保留'))
        self.assertIn('教师保留', {row['id']: row for row in chosen['materials']}[added['material_id']]['use'])
        first = retrieval.kept_id(offered[0])
        for keep in ([first, 'M999#0@' + '0' * 16], [first, 'M11#0'], [first[:-1] + ('0' if first[-1] != '0' else '1')]):
            with self.subTest(keep=keep), self.assertRaises(retrieval.RetrievalError) as refused:
                self.context(keep_chunks=keep)
            self.assertEqual(refused.exception.code, 'stale_selection')
        # The text of a kept chunk changes after the teacher saw it (the key itself still exists).
        with self.fixture_db() as conn:
            head = offered[0]['content'][:12]
            conn.execute('UPDATE materials SET content = replace(content, %s, %s) WHERE id = %s',
                         (head, head + '（已修改）', offered[0]['material_id']))
        with self.assertRaises(retrieval.RetrievalError) as refused:
            self.context(keep_chunks=[first])
        self.assertEqual(refused.exception.code, 'stale_selection')

    def test_keep_lists_are_validated_before_anything_runs(self):
        self.assertIsNone(module.retrieval_keep({}, 'hybrid'))
        good = ['M11#0@' + 'a' * 16, 'M16#2@' + '0123456789abcdef']
        self.assertEqual(module.retrieval_keep({'retrieval_chunk_ids': good}, 'hybrid'), good)
        for keep, mode in ((good[:1], 'sql'), (good[:1] * 2, 'hybrid'), (['M11#0'], 'hybrid'), (['M11@' + 'a' * 16], 'hybrid'),
                           (['M11#0@' + 'A' * 16], 'hybrid'), (good[0], 'hybrid'), ([11], 'hybrid'),
                           ([f'M{n}#0@' + 'a' * 16 for n in range(41)], 'hybrid'), ([], 'hybrid')):
            with self.subTest(keep=keep, mode=mode), self.assertRaises(module.JobError):
                module.retrieval_keep({'retrieval_chunk_ids': keep}, mode)

    def test_a_batch_never_reuses_one_learners_choice(self):
        seen = []

        def build(each):
            seen.append(each)
            raise module.JobError('隔离测试：不排队。')

        with patch.object(module, 'build_job', build):
            response = self.client.post('/api/jobs/batch', json={
                'topic': 'probability_advanced', 'retrieval_mode': 'hybrid', 'student_ids': [11, 12],
                'retrieval_chunk_ids': ['M11#0@' + 'a' * 16], 'class_id': 1})
        self.assertEqual(response.status_code, 202)
        self.assertEqual([each['student_id'] for each in seen], [11, 12])
        self.assertTrue(all('retrieval_chunk_ids' not in each and 'class_id' not in each for each in seen))

    def test_the_none_condition_gives_the_model_no_material(self):
        ctx = module.get_context(11, 'probability_advanced', 'profile', 'none')
        self.assertEqual(ctx['materials'], [])
        self.assertEqual(ctx['retrieval']['mode'], 'none')
        self.assertIn('不提供任何课程材料', ctx['retrieval']['note'])

    # ------------------------------------------------------------------ what the model receives
    def test_a_hybrid_request_has_the_rules_but_not_the_retrieval_record(self):
        hybrid, exact = self.context(), module.get_context(11, 'probability_advanced')
        prepared = prompts.prepare_request(hybrid, hybrid['topic']['goal'], 'fixture-model')
        system, user = (message['content'] for message in json.loads(prepared.body)['messages'])
        self.assertIn('【课程资料来自整门课的检索】', system)
        self.assertIn('+lecture-retrieval-v1', prepared.prompt_version)
        sent = json.loads(user)['context']
        self.assertNotIn('retrieval', sent)            # no runners-up, no removed chunks, no queries
        self.assertEqual([m['citation_id'] for m in sent['materials']], [f"M{m['id']}" for m in hybrid['materials']])
        self.assertTrue(all(m['use'] for m in sent['materials']))
        plain = prompts.prepare_request(exact, exact['topic']['goal'], 'fixture-model')
        self.assertNotIn('整门课的检索', plain.body)
        self.assertNotIn('lecture-retrieval', plain.prompt_version)

    def test_a_segment_gets_what_its_design_cites_and_what_matches_its_text(self):
        materials = [{'citation_id': 'M11', 'knowledge_id': 11, 'content': '条件概率的分母必须为正。'},
                     {'citation_id': 'M14', 'knowledge_id': 14, 'content': '蒙特卡洛方法用随机投点估计圆周率。'},
                     {'citation_id': 'M16', 'knowledge_id': 16, 'content': '两条产线的次品来源，用贝叶斯公式求后验。'}]
        segment = {'title': '例题：次品来自哪条产线', 'screen': '', 'board': ['贝叶斯公式'], 'teacher': ['先算后验'],
                   'questions': [], 'items': []}
        picked = designed.segment_materials(materials, segment, {}, {'M11'}, {11})
        self.assertEqual([m['citation_id'] for m in picked], ['M11', 'M16'])
        empty = {'title': '', 'screen': '', 'board': [], 'teacher': [], 'questions': [], 'items': []}
        self.assertEqual([m['citation_id'] for m in designed.segment_materials(materials, empty, {}, set(), {14})], ['M14'])

    # ------------------------------------------------------------------ checks on the written lesson
    def test_a_citation_of_the_wrong_material_is_listed(self):
        context = {'materials': [
            {'id': 1, 'content': '移项：等式两边同时减去同一个数，等式仍成立。例：2x + 5 = 17，两边同减 5 得 2x = 12。'},
            {'id': 2, 'content': '贝叶斯公式：后验概率等于先验乘似然，再除以全概率；各后验之和为 1。'},
            {'id': 3, 'content': 'The Poisson distribution counts events in a fixed interval with rate lambda.'}]}
        wrong = '后验概率等于先验乘似然，再除以全概率，所以各后验之和为 1 [M1]。'
        report = quality.citation_support(wrong, context)
        self.assertEqual([(e['ids'], e['closest']) for e in report['suspect']], [(['M1'], 'M2')])
        right = quality.citation_support(wrong.replace('[M1]', '[M2]'), context)
        self.assertEqual((right['checked'], right['suspect']), (1, []))
        other = quality.citation_support('泊松分布描述固定时间内事件发生次数的规律 [M3]。\n依据 [M2]', context)
        self.assertEqual((other['checked'], other['skipped_language'], other['skipped_short']), (0, 1, 1))

    def test_the_usage_check_reports_how_the_retrieved_material_was_used(self):
        ctx = self.context()
        learner = {item['material_id'] for item in ctx['retrieval']['items']
                   if 'learner' in (item['role'], *item.get('also', []))}
        self.assertTrue(learner)
        check = lambda text: next(c for c in quality.check_output(text, ctx, ctx['topic']['goal'])['automatic']
                                  if c['code'] == 'retrieval_usage')
        self.assertEqual(check('# 标题\n## 一\n没有引用任何材料。')['status'], 'warning')
        cited = check('# 标题\n## 一\n' + ''.join(f'[M{i}]' for i in sorted(learner)))
        self.assertEqual(cited['status'], 'pass')
        self.assertIn('针对学情取来的材料已引用', cited['detail'])


if __name__ == '__main__':
    unittest.main()
