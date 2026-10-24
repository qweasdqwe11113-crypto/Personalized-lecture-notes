"""Course, ablation and real PostgreSQL queue contracts; never calls Qwen."""
import copy
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from unittest.mock import patch

from psycopg.types.json import Jsonb
from test_profiles import ProfileDatabaseFixture, DEMO, module
from experiment import build_manifest
from jobs import JobQueue
from profiles import retain_initial_evidence
from prompts import prepare_request
from runs import create_run, save_success


class CourseJobTests(ProfileDatabaseFixture):
    def setUp(self):
        super().setUp()
        seed=json.loads((DEMO/'university_seed.json').read_text())
        with self.fixture_db() as conn:
            for s in seed['students']:
                conn.execute('INSERT INTO students (id,name,subtitle,profile) VALUES (%s,%s,%s,%s)', tuple(s[k] for k in ['id','name','subtitle','profile']))
            for k in seed['knowledge_points']:
                conn.execute('INSERT INTO knowledge_points VALUES (%s,%s)', (k['id'],k['name']))
            for s in seed['states']:
                conn.execute('INSERT INTO student_knowledge (student_id,knowledge_id,state,evidence) VALUES (%s,%s,%s,%s)',s)
            for m in seed['materials']:
                conn.execute('INSERT INTO materials VALUES (%s,%s,%s,%s,%s,%s,%s)', tuple(m[k] for k in ['id','knowledge_id','title','content','source_title','source_url','section']))
            retain_initial_evidence(conn)
        self.tasks=JobQueue(lambda: module.db(), module.execute_job)
        self.task_patch=patch.object(module,'TASKS',self.tasks);self.task_patch.start()
        self.gates=[]

    def tearDown(self):
        for gate in self.gates:gate.set()
        self.tasks.wait_idle(5);self.tasks.close();self.task_patch.stop()

    def payload(self, **overrides):
        return dict(student_id=11,topic='probability_advanced',goal='',personalization_mode='profile',**overrides)

    def post(self, payload=None, client=None):
        return (client or self.client).post('/api/jobs',json=payload or self.payload())

    def model(self, prepared, on_attempt):
        on_attempt()
        return module.ModelResult('# 隔离概率讲义\nP(D) = 0.032 [M11][M12][M13]', {'total_tokens':10})

    def count(self, name):
        with self.fixture_db() as conn:return conn.execute(f'SELECT count(*) AS n FROM {name}').fetchone()['n']

    def test_course_material_isolation_and_task_level_are_not_student_state(self):
        basic=module.get_context(11,'probability_basic');advanced=module.get_context(11,'probability_advanced')
        self.assertEqual([m['id'] for m in advanced['materials']],[11,12,13])
        self.assertEqual(basic['states'],advanced['states'])
        self.assertNotEqual(basic['topic']['task_level'],advanced['topic']['task_level'])
        self.assertEqual([s['state'] for s in basic['states']],['needs_support','unknown','partial'])
        self.assertIsNone(module.get_context(1,'probability_basic'))
        self.assertEqual([m['id'] for m in module.get_context(1,'linear')['materials']],[1,2,3])
        prepared=prepare_request(advanced,advanced['topic']['goal'],module.MODEL)
        self.assertEqual((prepared.prompt_version,prepared.input_version),('lecture-probability-v2+expanded-v1','lecture-context-v4'))

    def test_ablation_removes_selected_learner_fields_and_has_identical_actual_bytes(self):
        bodies=[]
        for sid in [11,12,13]:
            ctx=module.get_context(sid,'probability_advanced','none')
            self.assertEqual(ctx['states'],[])
            self.assertEqual(set(ctx['student']),{'name','profile'})
            body=prepare_request(ctx,ctx['topic']['goal'],module.MODEL).body;bodies.append(body)
            for forbidden in ['小许','小唐','小顾','recent_evidence','needs_support','profile_revision']:
                self.assertNotIn(forbidden,body)
        self.assertEqual(len(set(bodies)),1)
        user=json.loads(json.loads(bodies[0])['messages'][1]['content'])['context']
        self.assertNotIn('student_id',user)
        self.assertEqual(user['topic']['student_ids'],[11,12,13])  # Shared course cohort, not a selected learner.

        with patch.object(module,'call_qwen',self.model):
            response=self.client.post('/api/generate',json={'student_id':12,'topic':'probability_advanced','personalization_mode':'none'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()['context']['states'],[])

    def test_manifest_has_all_paired_cells_fixed_material_goal_parameters_and_repeats(self):
        m=build_manifest();self.assertEqual(len(m['cells']),12)
        self.assertEqual(len({c['goal'] for c in m['cells']}),1)
        self.assertEqual(len({c['sha256'] for c in m['cells'] if c['mode']=='none'}),1)
        self.assertEqual({(c['student_id'],c['mode'],c['repeat']) for c in m['cells']}, {(s,mode,r) for s in [11,12,13] for mode in ['profile','none'] for r in [1,2]})
        for c in m['cells']:
            self.assertEqual(hashlib.sha256(c['request_body'].encode()).hexdigest(),c['sha256'])

    def test_university_check_uses_its_length_and_probability_scope(self):
        from quality import check_output
        ctx=module.get_context(11,'probability_advanced')
        report=check_output('# 概率\n互斥完备 P(B)>0\n'+('概'*4500)+' [M13]',ctx,ctx['topic']['goal'])
        self.assertEqual(report['version'],'probability-text-check-v2')
        self.assertEqual(report['scope']['warnings'],[])
        self.assertEqual(next(c for c in report['automatic'] if c['code']=='length')['status'],'pass')
        legacy=copy.deepcopy(ctx);legacy.pop('lecture_length_policy')
        historical=check_output('概'*950,legacy,legacy['topic']['goal'])
        self.assertEqual(next(c for c in historical['automatic'] if c['code']=='length')['status'],'pass')
        self.assertEqual(next(c for c in report['automatic'] if c['code']=='probability_conditions')['status'],'pass')
        self.assertTrue(all(c['status']=='needs_review' for c in report['teacher_review']))

    def test_queue_capacity_fifo_and_submit_time_snapshot(self):
        entered=Event();release=Event();self.gates.append(release);calls=[]
        def model(prepared,on_attempt):
            calls.append(prepared.body)
            if len(calls)==1:entered.set();release.wait(5)
            return self.model(prepared,on_attempt)
        with patch.object(module,'call_qwen',model):
            first=self.post();self.assertEqual(first.status_code,202);self.assertTrue(entered.wait(2))
            queued=[self.post().get_json() for _ in range(3)]
            self.assertEqual(self.post().status_code,429)
            self.assertEqual(self.count('generation_runs'),4)
            self.assertEqual([self.tasks.detail(j['id'])['queue_position'] for j in queued],[1,2,3])
            with self.fixture_db() as conn:conn.execute("UPDATE students SET profile='提交后改动' WHERE id=11")
            release.set();self.assertTrue(self.tasks.wait_idle(5))
        self.assertEqual(len(calls),4)
        self.assertTrue(all('提交后改动' not in body for body in calls))
        self.assertTrue(all(self.tasks.detail(j['id'])['status']=='succeeded' for j in queued))
        self.assertEqual(self.tasks.detail(first.get_json()['id'])['usage']['total_tokens'],10)

    def test_two_simultaneous_requests_are_accepted_and_model_is_serial(self):
        entered=Event();release=Event();self.gates.append(release);guard=Lock();counts={'active':0,'peak':0,'calls':0}
        def model(prepared,on_attempt):
            with guard:
                counts['active']+=1;counts['peak']=max(counts['peak'],counts['active']);counts['calls']+=1
                is_first=counts['calls']==1
            if is_first:entered.set();release.wait(5)
            output=self.model(prepared,on_attempt)
            with guard:counts['active']-=1
            return output
        def submit(_):
            with module.app.test_client() as c:return self.post(client=c)
        with patch.object(module,'call_qwen',model), ThreadPoolExecutor(max_workers=2) as pool:
            replies=list(pool.map(submit,[1,2]));self.assertTrue(entered.wait(2))
            self.assertEqual([r.status_code for r in replies],[202,202])
            release.set();self.assertTrue(self.tasks.wait_idle(5))
        self.assertEqual((counts['calls'],counts['peak']),(2,1))
        self.assertEqual(self.count('lectures'),3)

    def test_failed_job_retry_preserves_exact_input_and_original_failure(self):
        def fail(prepared,on_attempt):on_attempt();raise module.ModelError('timeout','测试超时')
        with patch.object(module,'call_qwen',fail):
            job=self.post().get_json();self.assertTrue(self.tasks.wait_idle(5))
        with self.fixture_db() as conn:
            original=conn.execute('SELECT * FROM generation_runs WHERE id=%s',(job['run_id'],)).fetchone()
            conn.execute("UPDATE students SET profile='重试前改动' WHERE id=11")
        with patch.object(module,'call_qwen',self.model):
            retry=self.client.post(f'/api/generation-runs/{job["run_id"]}/retry');self.assertEqual(retry.status_code,202)
            self.assertTrue(self.tasks.wait_idle(5))
        second=retry.get_json()
        with self.fixture_db() as conn:
            self.assertEqual(conn.execute('SELECT * FROM generation_runs WHERE id=%s',(job['run_id'],)).fetchone(),original)
            run=conn.execute('SELECT * FROM generation_runs WHERE id=%s',(second['run_id'],)).fetchone()
        self.assertEqual(run['request_body'],original['request_body']);self.assertEqual(run['context'],original['context'])
        self.assertEqual(second['parent_run_id'],job['run_id'])
        self.assertEqual(run['status'],'succeeded')
        self.assertEqual(self.client.post(f'/api/generation-runs/{run["id"]}/retry').status_code,409)

    def test_retry_of_tampered_or_missing_input_is_rejected(self):
        def fail(prepared,on_attempt):raise module.ModelError('missing_key','测试缺失密钥','configuration')
        with patch.object(module,'call_qwen',fail):
            job=self.post().get_json();self.assertTrue(self.tasks.wait_idle(5))
        with self.fixture_db() as conn:conn.execute("UPDATE generation_runs SET request_body=request_body||'x' WHERE id=%s",(job['run_id'],))
        self.assertEqual(self.client.post(f'/api/generation-runs/{job["run_id"]}/retry').status_code,409)
        self.assertEqual(self.client.post('/api/generation-runs/999/retry').status_code,404)
        self.assertEqual(self.count('generation_runs'),1)

    def test_submit_transaction_rollback_leaves_no_half_input_or_job(self):
        with self.fixture_db() as conn:conn.execute("ALTER TABLE generation_jobs ADD CONSTRAINT reject_queued CHECK (status<>'queued')")
        self.assertEqual(self.post().status_code,503)
        self.assertEqual(self.count('generation_runs'),0);self.assertEqual(self.count('generation_jobs'),0)

    def test_job_completion_and_success_lecture_are_atomic(self):
        with self.fixture_db() as conn:conn.execute("ALTER TABLE generation_jobs ADD CONSTRAINT reject_success CHECK (status<>'succeeded')")
        with patch.object(module,'call_qwen',self.model):
            job=self.post().get_json();self.assertTrue(self.tasks.wait_idle(5))
        row=self.tasks.detail(job['id']);self.assertEqual(row['status'],'failed');self.assertIsNone(row['lecture_id'])
        self.assertEqual(self.count('lectures'),1)
        with self.fixture_db() as conn:
            run=conn.execute('SELECT * FROM generation_runs WHERE id=%s',(job['run_id'],)).fetchone()
        self.assertEqual(run['status'],'failed');self.assertIn('P(D)',run['content'])

    def test_restart_recovers_queue_without_resending_and_keeps_completed_run(self):
        ctx=module.get_context(11,'probability_advanced');prepared=prepare_request(ctx,ctx['topic']['goal'],module.MODEL)
        with self.fixture_db() as conn:
            a=create_run(conn,11,'probability_advanced',ctx['topic']['goal'],ctx,prepared)
            b=create_run(conn,11,'probability_advanced',ctx['topic']['goal'],ctx,prepared)
            row=save_success(conn,b,11,ctx['topic']['goal'],ctx,prepared,module.ModelResult('# 完成 [M13]'),1,{'has_citations':True})
            conn.execute("INSERT INTO generation_jobs (id,run_id,status) VALUES ('orphan-a',%s,'queued'),('orphan-b',%s,'running')",(a,b))
        with patch.object(module,'call_qwen') as model:self.tasks.recover();model.assert_not_called()
        self.assertEqual(self.tasks.detail('orphan-a')['status'],'interrupted')
        self.assertEqual(self.tasks.detail('orphan-b')['lecture_id'],row['id'])
        with self.fixture_db() as conn:self.assertEqual(conn.execute('SELECT error_code FROM generation_runs WHERE id=%s',(a,)).fetchone()['error_code'],'process_interrupted')

    def test_validation_cross_origin_and_missing_job_never_submit(self):
        for bad in [[],{'student_id':True,'topic':'linear'},{'student_id':11,'topic':'no'},{'student_id':11,'topic':'probability_basic','goal':'字'*401},{'student_id':11,'topic':'probability_basic','personalization_mode':[]}]:
            self.assertEqual(self.client.post('/api/jobs',json=bad).status_code,400)
        self.assertEqual(self.client.post('/api/jobs',json=self.payload(),headers={'Origin':'https://outside.invalid'}).status_code,403)
        self.assertEqual(self.client.get('/api/jobs/missing').status_code,404)
        self.assertEqual(self.count('generation_runs'),0)

    def test_health_sanitizes_configuration_and_does_not_call_model(self):
        with patch.dict(os.environ,{'QWEN_API_KEY':'fake-test-secret','QWEN_BASE_URL':'https://user:fake-pass@model.invalid/v1'}),patch.object(module,'call_qwen') as model:
            response=self.client.get('/api/health');self.assertEqual(response.status_code,200)
            h=response.get_json();self.assertTrue(h['key_present']);self.assertFalse(h['endpoint_format_ok']);model.assert_not_called()
            self.assertNotIn('fake-test-secret',response.get_data(as_text=True));self.assertNotIn('fake-pass',response.get_data(as_text=True))
        with patch.dict(os.environ,{'QWEN_BASE_URL':'http://[invalid'}):self.assertFalse(self.client.get('/api/health').get_json()['endpoint_format_ok'])

    def test_truncated_job_keeps_partial_check_and_no_success(self):
        def truncated(prepared,on_attempt):
            on_attempt();raise module.ModelError('truncated','测试截断','output',module.ModelResult('# 部分概率 [M13]',{'total_tokens':12}))
        with patch.object(module,'call_qwen',truncated):
            job=self.post().get_json();self.assertTrue(self.tasks.wait_idle(5))
        run=self.client.get(f'/api/generation-runs/{job["run_id"]}').get_json()
        self.assertEqual(run['status'],'failed');self.assertIsNone(run['lecture_id']);self.assertEqual(run['usage']['total_tokens'],12)
        self.assertEqual(run['citation_check']['quality_report']['version'],'probability-text-check-v2')

    def test_new_hints_preserve_frozen_old_rules_and_do_not_certify_math(self):
        from quality import check_output
        ctx=module.get_context(11,'probability_advanced')
        report=check_output('P(B) = Σi P(Ai∩Bi)。仅在 P(A)=P(B) 时相等。',ctx,ctx['topic']['goal'])
        self.assertEqual(next(c for c in report['automatic'] if c['code']=='known_probability_hints')['status'],'warning')
        old=copy.deepcopy(ctx);old['topic'].pop('check_version');old['topic']['length_range']=[900,1400]
        earlier=check_output('概'*700,old,old['topic']['goal'])
        self.assertEqual(earlier['version'],'probability-text-check-v1')
        self.assertEqual(next(c for c in earlier['automatic'] if c['code']=='length')['status'],'warning')
        self.assertTrue(all(c['status']=='needs_review' for c in report['teacher_review']))
