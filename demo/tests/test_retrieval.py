"""Retrieval checks in a disposable schema. No embedding or generation service is called."""
import json
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_profiles import ProfileDatabaseFixture, DEMO
import app as module
import embed
import quality
import retrieval
from profiles import retain_initial_evidence


class RetrievalTests(ProfileDatabaseFixture):
    def setUp(self):
        super().setUp()
        seed = json.loads((DEMO / 'university_seed.json').read_text(encoding='utf-8'))
        with self.fixture_db() as conn:
            for row in seed['students']:
                conn.execute('INSERT INTO students (id,name,subtitle,profile) VALUES (%s,%s,%s,%s)',
                             tuple(row[k] for k in ['id', 'name', 'subtitle', 'profile']))
            for row in seed['knowledge_points']:
                conn.execute('INSERT INTO knowledge_points VALUES (%s,%s)', (row['id'], row['name']))
            for row in seed['states']:
                conn.execute('INSERT INTO student_knowledge (student_id,knowledge_id,state,evidence) VALUES (%s,%s,%s,%s)', row)
            for row in seed['materials']:
                conn.execute('INSERT INTO materials VALUES (%s,%s,%s,%s,%s,%s,%s)',
                             tuple(row[k] for k in ['id', 'knowledge_id', 'title', 'content',
                                                    'source_title', 'source_url', 'section']))
            retain_initial_evidence(conn)
        self.embedder = embed.HashingEmbedder(dimensions=128)
        self.embed_patch = patch.object(module, 'EMBEDDER', self.embedder)
        self.embed_patch.start()

    def tearDown(self):
        self.embed_patch.stop()
        super().tearDown()

    def chunks(self):
        with self.fixture_db() as conn:
            return conn.execute('SELECT count(*) AS n FROM material_chunks').fetchone()['n']

    def index_all(self):
        with self.fixture_db() as conn:
            retrieval.index_knowledge(conn, self.embedder, [11, 12, 13])

    def test_chunking_is_deterministic_bounded_and_paragraph_aware(self):
        text = '第一段的小结。\n' + '条件概率的定义与解释。' * 60
        pieces = retrieval.chunk_text(text)
        self.assertEqual(pieces, retrieval.chunk_text(text))
        self.assertTrue(all(len(piece) <= retrieval.CHUNK_SIZE for piece in pieces))
        self.assertTrue(all(piece.strip() for piece in pieces))
        self.assertEqual(retrieval.chunk_text('很短的一段'), ['很短的一段'])
        self.assertEqual(retrieval.chunk_text('   \n\n  '), [])
        self.assertIn('第一段的小结。', pieces[0])

    def test_cosine_validates_dimensions(self):
        self.assertAlmostEqual(retrieval.cosine([1.0, 0.0], [1.0, 0.0]), 1.0)
        self.assertAlmostEqual(retrieval.cosine([1.0, 0.0], [0.0, 0.0]), 0.0)
        with self.assertRaises(retrieval.RetrievalError):
            retrieval.cosine([1.0, 0.0], [1.0, 0.0, 0.0])

    def test_hashing_embedder_is_offline_and_deterministic(self):
        embedder = embed.HashingEmbedder(dimensions=64)
        self.assertEqual(embedder.embed(['贝叶斯']), embedder.embed(['贝叶斯']))
        self.assertEqual(len(embedder.embed(['贝叶斯'])[0]), 64)
        plain = embedder.embed(['贝叶斯公式'])[0]
        related = embedder.embed(['贝叶斯公式与后验'])[0]
        far = embedder.embed(['两步方程与移项依据'])[0]
        self.assertAlmostEqual(retrieval.cosine(plain, plain), 1.0)
        self.assertGreater(retrieval.cosine(plain, related), retrieval.cosine(plain, far))

    def test_indexing_is_idempotent_and_status_reports_the_model_in_use(self):
        with self.fixture_db() as conn:
            material = conn.execute('SELECT * FROM materials WHERE id = 11').fetchone()
            count = retrieval.index_material(conn, self.embedder, material)
        self.assertEqual(count, self.chunks())
        with self.fixture_db() as conn:
            material = conn.execute('SELECT * FROM materials WHERE id = 11').fetchone()
            self.assertEqual(retrieval.index_material(conn, self.embedder, material), count)
        self.assertEqual(self.chunks(), count)
        with self.fixture_db() as conn:
            state = retrieval.status(conn, self.embedder, [11, 12, 13])
        self.assertEqual(state['embed_model'], self.embedder.model)
        self.assertEqual(state['backend'], 'array-cosine')
        self.assertTrue(all(point['indexed'] for point in state['points'] if point['knowledge_id'] == 11))
        self.assertFalse(any(point['indexed'] for point in state['points'] if point['knowledge_id'] in (12, 13)))

    def test_search_ranks_by_score_and_shapes_chunks_like_materials(self):
        self.index_all()
        with self.fixture_db() as conn:
            result = retrieval.search(conn, self.embedder, '贝叶斯后验', [11, 12, 13])
        scores = [item['score'] for item in result['items']]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(result['mode'], 'vector')
        self.assertEqual(result['backend'], 'array-cosine')
        self.assertLessEqual(len(result['items']), 6)
        for row in retrieval.to_materials(result['items']):
            self.assertEqual(row['citation_id'], f"M{row['id']}")
            self.assertIn('source_title', row)
            self.assertTrue(row['content'].strip())

    def test_multiple_chunks_of_one_material_keep_one_citation(self):
        with self.fixture_db() as conn:
            conn.execute('''INSERT INTO materials VALUES (99, 11, '长材料', %s, '来源', 'http://x', '节')''',
                         ('条件概率要求条件事件概率大于零。\n' * 40,))
            material = conn.execute('SELECT * FROM materials WHERE id = 99').fetchone()
            self.assertGreater(retrieval.index_material(conn, self.embedder, material), 1)
            items = retrieval.search(conn, self.embedder, '条件概率', [11], limit=8)['items']
        same = [item for item in items if item['material_id'] == 99]
        materials = retrieval.to_materials(same)
        self.assertEqual(len(materials), 1)
        self.assertEqual(materials[0]['chunk_count'], len(same))
        self.assertEqual(materials[0]['citation_id'], 'M99')

    def test_search_refuses_unindexed_knowledge_points(self):
        with self.fixture_db() as conn:
            with self.assertRaises(retrieval.RetrievalError):
                retrieval.search(conn, self.embedder, '任意问题', [11, 12, 13])

    def test_build_query_keeps_the_ablation_clean(self):
        states = [{'name': '条件概率的方向', 'state': 'needs_support'},
                  {'name': '全概率', 'state': 'mastered'}]
        query = retrieval.build_query('推导贝叶斯公式', states)
        self.assertIn('条件概率的方向', query)
        self.assertNotIn('全概率', query)
        self.assertEqual(retrieval.build_query('推导贝叶斯公式', []), '推导贝叶斯公式')

    def test_default_context_is_still_the_exact_sql_lookup(self):
        ctx = module.get_context(11, 'probability_advanced')
        self.assertEqual(ctx['retrieval']['mode'], 'sql')
        self.assertEqual(ctx['retrieval']['items'], [])
        self.assertIsNone(ctx['retrieval']['backend'])
        self.assertEqual([row['id'] for row in ctx['materials']], [11, 12, 13])

    def test_vector_mode_records_query_backend_and_items(self):
        # The first vector request prepares its own index.
        self.assertEqual(self.chunks(), 0)
        ctx = module.get_context(11, 'probability_advanced', 'profile', 'vector')
        self.assertEqual(ctx['retrieval']['mode'], 'vector')
        self.assertEqual(ctx['retrieval']['embed_model'], self.embedder.model)
        self.assertTrue(ctx['retrieval']['items'])
        self.assertIn('score', ctx['retrieval']['items'][0])
        self.assertTrue(all(row['citation_id'].startswith('M') for row in ctx['materials']))
        plain = module.get_context(11, 'probability_advanced', 'none', 'vector')
        self.assertNotIn('薄弱', plain['retrieval']['query'])
        self.assertIn('薄弱', ctx['retrieval']['query'])

    def test_auto_index_reuses_vectors_and_refreshes_changed_materials(self):
        module.get_context(11, 'probability_advanced', 'profile', 'vector')
        with patch.object(self.embedder, 'embed', wraps=self.embedder.embed) as calls:
            module.get_context(11, 'probability_advanced', 'profile', 'vector')
            self.assertEqual(calls.call_count, 1)  # Query only; stored vectors reused.
        with self.fixture_db() as conn:
            conn.execute("UPDATE materials SET content = '更新后的贝叶斯材料' WHERE id = 11")
        with patch.object(self.embedder, 'embed', wraps=self.embedder.embed) as calls:
            module.get_context(11, 'probability_advanced', 'profile', 'vector')
            self.assertEqual(calls.call_count, 2)  # One changed material plus query.
        with self.fixture_db() as conn:
            rows = conn.execute('SELECT content FROM material_chunks WHERE material_id = 11').fetchall()
        self.assertEqual([r['content'] for r in rows], ['更新后的贝叶斯材料'])

    def test_auto_index_replaces_model_and_chunk_boundaries(self):
        module.get_context(11, 'probability_advanced', 'profile', 'vector')
        with self.fixture_db() as conn:
            conn.execute("UPDATE materials SET content = %s WHERE id = 11", ('条件概率' * 180,))
        module.get_context(11, 'probability_advanced', 'profile', 'vector')
        with self.fixture_db() as conn:
            before = conn.execute('SELECT count(*) AS n FROM material_chunks WHERE material_id = 11').fetchone()['n']
        original = retrieval.chunk_text
        with patch.object(retrieval, 'chunk_text', side_effect=lambda text: original(text, size=120, overlap=20)):
            module.get_context(11, 'probability_advanced', 'profile', 'vector')
        with self.fixture_db() as conn:
            after = conn.execute('SELECT count(*) AS n FROM material_chunks WHERE material_id = 11').fetchone()['n']
        self.assertGreater(after, before)
        replacement = embed.HashingEmbedder(dimensions=64)
        with patch.object(module, 'EMBEDDER', replacement):
            ctx = module.get_context(11, 'probability_advanced', 'profile', 'vector')
        self.assertEqual(ctx['retrieval']['stored_embed_models'], [replacement.model])

    def test_sql_never_embeds_and_auto_index_errors_reach_the_ui(self):
        with patch.object(self.embedder, 'embed', side_effect=embed.EmbeddingError('unreachable', '嵌入服务不可达')):
            self.assertEqual(self.client.get('/api/context?student_id=11&topic=probability_advanced').status_code, 200)
            response = self.client.get('/api/context?student_id=11&topic=probability_advanced&retrieval_mode=vector')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json['error'], '嵌入服务不可达')

    def test_empty_material_removes_stale_vectors(self):
        self.index_all()
        with self.fixture_db() as conn:
            conn.execute("UPDATE materials SET content = '' WHERE id = 11")
        module.get_context(11, 'probability_advanced', 'profile', 'vector')
        with self.fixture_db() as conn:
            rows = conn.execute('SELECT id FROM material_chunks WHERE material_id = 11').fetchall()
        self.assertEqual(rows, [])

    def test_retrieval_check_only_appears_when_retrieval_was_used(self):
        self.index_all()
        content = '# 标题\n正文引用 [M11][M12][M13]'
        exact = module.get_context(11, 'probability_advanced')
        picked = module.get_context(11, 'probability_advanced', 'profile', 'vector')
        codes = lambda ctx: {item['code'] for item in quality.check_output(content, ctx, ctx['topic']['goal'])['automatic']}
        self.assertNotIn('retrieval_usage', codes(exact))
        self.assertIn('retrieval_usage', codes(picked))

    def test_status_index_endpoints_report_real_state(self):
        self.assertEqual(self.client.get('/api/retrieval/status?topic=bad').status_code, 400)
        self.assertEqual(self.client.get('/api/retrieval/status?topic=probability_advanced').json['total_chunks'], 0)
        response = self.client.post('/api/retrieval/index', json={'topic': 'probability_advanced'})
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.json['total_chunks'], 0)
        self.assertTrue(all(point['indexed'] for point in response.json['points']))
        self.assertEqual(self.client.post('/api/retrieval/index', json={}).status_code, 400)

    def test_adopt_is_idempotent_for_one_upload(self):
        record = {'title': '课件摘要', 'filename': 'lesson.txt', 'source_url': '',
                  'content': '全概率要求划分两两互斥且覆盖整个样本空间。'}
        with patch.object(module, 'read_upload', return_value=record):
            first = self.client.post('/api/retrieval/adopt', json={'upload_id': 'abc', 'knowledge_id': 12})
            second = self.client.post('/api/retrieval/adopt', json={'upload_id': 'abc', 'knowledge_id': 12})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json['material_id'], second.json['material_id'])
        self.assertEqual(first.json['citation_id'], f"M{first.json['material_id']}")
        with self.fixture_db() as conn:
            rows = conn.execute('SELECT * FROM materials WHERE source_url = %s', ('upload:abc',)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.client.post('/api/retrieval/adopt',
                                          json={'upload_id': 'abc', 'knowledge_id': 999}).status_code, 400)

    def test_generation_records_how_the_material_was_picked(self):
        self.index_all()

        def model(prepared, on_attempt):
            on_attempt()
            return module.ModelResult('# 隔离讲义\n参考来源 [M11]', {'total_tokens': 5})

        with patch.object(module, 'call_qwen', model):
            response = self.client.post('/api/generate', json={'student_id': 11, 'topic': 'probability_advanced',
                                                               'retrieval_mode': 'vector'})
        self.assertEqual(response.status_code, 200)
        with self.fixture_db() as conn:
            row = conn.execute('SELECT * FROM generation_runs WHERE id = %s',
                               (response.json['generation_run_id'],)).fetchone()
        self.assertEqual(row['retrieval']['mode'], 'vector')
        self.assertEqual(row['retrieval']['backend'], 'array-cosine')
        self.assertTrue(row['retrieval']['items'])
        self.assertEqual(row['retrieval']['embed_model'], self.embedder.model)
        self.assertEqual(self.client.post('/api/generate', json={'student_id': 11, 'topic': 'probability_advanced',
                                                                 'retrieval_mode': 'quantum'}).status_code, 400)

    def test_embedding_failures_never_reveal_the_endpoint_or_key(self):
        client = embed.QwenEmbedder(model='embed-model', base='http://172.18.36.44:8000/v1', key='secret-key')
        self.assertTrue(client.configured())
        failure = urllib.error.HTTPError('http://172.18.36.44:8000/v1/embeddings', 404, 'nope', {}, None)
        with patch.object(embed.urllib.request, 'urlopen', side_effect=failure):
            with self.assertRaises(embed.EmbeddingError) as caught:
                client.embed(['文本'])
        message = str(caught.exception)
        self.assertNotIn('172.18.36.44', message)
        self.assertNotIn('secret-key', message)
        self.assertIn('HTTP 404', message)


if __name__ == '__main__':
    unittest.main()
