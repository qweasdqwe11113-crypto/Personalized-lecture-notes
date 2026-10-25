"""Current-version comparisons freeze all inputs and retain failures without selection."""
import json
import time
from threading import Event
from unittest.mock import patch

from test_profiles import ProfileDatabaseFixture, DEMO, module
from jobs import JobQueue
from profiles import retain_initial_evidence


class CurrentEvaluationTests(ProfileDatabaseFixture):
    def setUp(self):
        super().setUp()
        seed=json.loads((DEMO/'university_seed.json').read_text())
        with self.fixture_db() as conn:
            for row in seed['students']:
                conn.execute('INSERT INTO students(id,name,subtitle,profile) VALUES (%s,%s,%s,%s)',tuple(row[k] for k in ('id','name','subtitle','profile')))
            for row in seed['knowledge_points']:conn.execute('INSERT INTO knowledge_points VALUES (%s,%s)',(row['id'],row['name']))
            for row in seed['states']:conn.execute('INSERT INTO student_knowledge(student_id,knowledge_id,state,evidence) VALUES (%s,%s,%s,%s)',row)
            for row in seed['materials']:conn.execute('INSERT INTO materials VALUES (%s,%s,%s,%s,%s,%s,%s)',tuple(row[k] for k in ('id','knowledge_id','title','content','source_title','source_url','section')))
            retain_initial_evidence(conn)
        self.queue=JobQueue(lambda:module.db(),module.execute_job)
        self.queue_patch=patch.object(module,'TASKS',self.queue);self.queue_patch.start()

    def tearDown(self):
        self.queue.wait_idle(5);self.queue.close();self.queue_patch.stop()

    def prepare(self):
        response=self.client.post('/api/lecture-experiments')
        self.assertEqual(response.status_code,201,response.json)
        return response.json['id']

    def wait(self,identity):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            row=self.client.get('/api/lecture-experiments/'+identity).json
            if row['status']!='running':return row
            time.sleep(.02)
        self.fail('comparison did not finish')

    def test_prepare_is_offline_and_freezes_paired_inputs(self):
        with patch.object(module,'call_qwen',side_effect=AssertionError('prepare cannot call model')):
            identity=self.prepare()
        with self.fixture_db() as conn:rows=conn.execute('SELECT snapshot FROM lecture_experiment_cells WHERE experiment_id=%s ORDER BY cell',(identity,)).fetchall()
        cells=[r['snapshot'] for r in rows]
        self.assertEqual(len(cells),12)
        self.assertEqual(len({c['sha256'] for c in cells if c['mode']=='none'}),1)
        self.assertEqual(len({c['prompt_version'] for c in cells}),1)
        self.assertEqual(len({json.loads(c['request_body'])['messages'][0]['content'] for c in cells}),1)
        for c in cells:
            self.assertEqual(c['context']['teaching_options']['length_policy'],'duration-v2')
            if c['mode']=='none':self.assertEqual(c['context']['personalization_plan'],[])

    def test_probability_copies_keep_conditions_examples_and_diagnostic_identity(self):
        from prompts import prepare_request
        from quality import check_output
        for key,count in [('probability_basic',1),('probability_advanced',2)]:
            original=module.get_context(11,key)
            copied=self.client.post('/api/course-tasks/'+key+'/copy')
            self.assertEqual(copied.status_code,201,copied.json)
            ctx=module.get_context(11,copied.json['key'])
            self.assertEqual([s['state'] for s in ctx['states']],[s['state'] for s in original['states']])
            self.assertEqual(len(ctx['reference_examples']),count)
            self.assertTrue(prepare_request(ctx,ctx['topic']['goal'],'fixture').prompt_version.startswith('lecture-probability-v2'))
            report=check_output('# 标题\n互斥完备，P(B)>0',ctx,ctx['topic']['goal'])
            self.assertIn('probability_conditions',[c['code'] for c in report['automatic']])
            self.assertEqual([p['scope'] for p in report['scope']['coverage']], [p['scope'] for p in original['scope_check']['coverage']])

    def test_run_keeps_failure_and_blocks_interleaved_generation(self):
        identity=self.prepare();entered=Event();release=Event();calls=[]
        def model(prepared,attempted):
            calls.append(prepared.sha256);attempted()
            if len(calls)==1:entered.set();release.wait(5)
            return module.ModelResult('# 真实调用的隔离替身\n'+('[V999]' if len(calls)==2 else '[M11][M12][M13]'))
        with patch.object(module,'call_qwen',model):
            try:
                response=self.client.post('/api/lecture-experiments/'+identity+'/run')
                self.assertEqual(response.status_code,202,response.json)
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.client.post('/api/jobs',json=dict(student_id=2,topic='linear')).status_code,409)
            finally:release.set()
            row=self.wait(identity)
        self.assertEqual(len(calls),12)
        self.assertEqual(row['summary']['completed'],11)
        self.assertEqual(row['summary']['failed'],1)
        self.assertEqual(row['summary']['teacher_reviewed'],0)
        self.assertEqual(row['summary']['timing_records'],0)
        self.assertEqual(self.client.post('/api/lecture-experiments/'+identity+'/run').status_code,409)
        self.assertEqual(len(calls),12)

    def test_corrupt_input_is_not_dispatched(self):
        identity=self.prepare()
        with self.fixture_db() as conn:
            conn.execute("UPDATE lecture_experiment_cells SET status='interrupted' WHERE experiment_id=%s AND cell<>1",(identity,))
            conn.execute("UPDATE lecture_experiment_cells SET snapshot=jsonb_set(snapshot,'{request_body}','\"tampered\"') WHERE experiment_id=%s AND cell=1",(identity,))
        with patch.object(module,'call_qwen',side_effect=AssertionError('corrupt request must not dispatch')) as model:
            self.assertEqual(self.client.post('/api/lecture-experiments/'+identity+'/run').status_code,202)
            row=self.wait(identity);model.assert_not_called()
        self.assertEqual(row['cells'][0]['status'],'failed')

    def test_restart_retains_a_known_success_and_never_resends_unknown_dispatch(self):
        identity=self.prepare()
        with self.fixture_db() as conn:
            snapshot=conn.execute('SELECT snapshot FROM lecture_experiment_cells WHERE experiment_id=%s AND cell=1',(identity,)).fetchone()['snapshot']
        from prompts import PreparedRequest
        prepared=PreparedRequest(snapshot['request_body'],snapshot['sha256'],snapshot['prompt_version'],snapshot['input_version'])
        with patch.object(module,'call_qwen',return_value=module.ModelResult('# 测试成功 [M11]')):
            job=self.queue.submit(snapshot['student_id'],snapshot['topic'],snapshot['goal'],snapshot['context'],prepared)
            self.assertTrue(self.queue.wait_idle(5))
        with self.fixture_db() as conn:
            conn.execute("UPDATE lecture_experiment_cells SET status='queued',job_id=%s WHERE experiment_id=%s AND cell=1",(job['id'],identity))
            conn.execute("UPDATE lecture_experiment_cells SET status='dispatching' WHERE experiment_id=%s AND cell=2",(identity,))
            conn.execute("UPDATE lecture_experiments SET status='running' WHERE id=%s",(identity,))
        with patch.object(module,'call_qwen',side_effect=AssertionError('recovery cannot call model')):
            module.recover_evaluation()
        result=self.client.get('/api/lecture-experiments/'+identity).json
        self.assertEqual(result['status'],'interrupted')
        self.assertEqual([c['status'] for c in result['cells'][:3]],['succeeded','interrupted','prepared'])
