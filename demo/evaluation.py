"""Frozen current-version comparisons in PostgreSQL. No report directories or invented reviews."""
import hashlib
import json
from threading import Lock, Thread
from uuid import uuid4

from flask import jsonify, request
from psycopg.types.json import Jsonb

from lecture_policy import finalize_context
from prompts import prepare_request, PreparedRequest
from teaching import DEFAULTS


def register_evaluation(app, db, queue, context_builder, model, other_tasks_idle=lambda: True):
    gate = Lock()

    def recover():
        with db() as conn:
            conn.execute("UPDATE lecture_experiments SET status='interrupted' WHERE status='running'")
            conn.execute("""UPDATE lecture_experiment_cells c SET status='succeeded',error=''
                FROM generation_jobs j WHERE c.job_id=j.id AND c.status IN ('dispatching','queued')
                AND j.status='succeeded'""")
            conn.execute("""UPDATE lecture_experiment_cells SET status='interrupted',error='服务重启；请核对关联生成记录。未知结果不自动重发。'
                WHERE status IN ('dispatching','queued')""")

    def detail(identity):
        with db() as conn:
            experiment=conn.execute('SELECT * FROM lecture_experiments WHERE id=%s',(identity,)).fetchone()
            if not experiment:return None
            rows=conn.execute('''SELECT c.cell,c.status,c.error,c.job_id,c.snapshot->>'mode' AS mode,
                c.snapshot->>'student_id' AS student_id,c.snapshot->>'repeat' AS repeat,
                c.snapshot->>'sha256' AS sha256,r.lecture_id,r.id AS run_id,r.elapsed_seconds,r.error_message,
                v.status AS review_status,v.review_seconds,v.checklist,v.recorder
                FROM lecture_experiment_cells c LEFT JOIN generation_jobs j ON j.id=c.job_id
                LEFT JOIN generation_runs r ON r.id=j.run_id
                LEFT JOIN LATERAL (SELECT status,review_seconds,checklist,recorder FROM lecture_revisions
                    WHERE lecture_id=r.lecture_id ORDER BY revision_number DESC LIMIT 1) v ON true
                WHERE c.experiment_id=%s ORDER BY c.cell''',(identity,)).fetchall()
        experiment['cells']=rows
        experiment['summary']=dict(total=len(rows),completed=sum(r['status']=='succeeded' for r in rows),
            failed=sum(r['status'] in ('failed','interrupted') for r in rows),
            teacher_reviewed=sum(r['review_status']=='reviewed' for r in rows),
            recorded_review_seconds=sum(r['review_seconds'] or 0 for r in rows),
            timing_records=sum(bool(r['review_seconds']) for r in rows))
        return experiment

    @app.before_request
    def protect_comparison():
        if gate.locked() and request.method=='POST' and (request.path in ('/api/jobs','/api/generate','/api/quizzes','/api/grading') or request.path.endswith('/section-rewrites') or request.path.endswith('/retry')):
            return jsonify(error='固定对照正在顺序执行，请完成后再提交其他生成。'),409

    @app.post('/api/lecture-experiments')
    def prepare():
        if gate.locked() or not queue().wait_idle(0) or not other_tasks_idle():
            return jsonify(error='请等待当前讲义任务结束后准备固定对照。'),409
        cells=[];topic='probability_advanced';requested_model=model()
        for repeat in (1,2):
            for sid in (11,12,13):
                for mode in ('profile','none'):
                    ctx=context_builder(sid,topic,mode)
                    if not ctx:return jsonify(error='缺少大学概率专题或虚拟画像，请先初始化。'),400
                    goal=ctx['topic']['goal']
                    ctx['teaching_options']={**DEFAULTS,'length_policy':'duration-v2'}
                    ctx=finalize_context(ctx,goal)
                    prepared=prepare_request(ctx,goal,requested_model)
                    cells.append(dict(cell=len(cells)+1,student_id=sid,repeat=repeat,mode=mode,topic=topic,goal=goal,
                        context=ctx,request_body=prepared.body,sha256=prepared.sha256,
                        prompt_version=prepared.prompt_version,input_version=prepared.input_version))
        material_hashes={hashlib.sha256(json.dumps(c['context']['materials'],sort_keys=True,ensure_ascii=False).encode()).hexdigest() for c in cells}
        none_hashes={c['sha256'] for c in cells if c['mode']=='none'}
        versions={c['prompt_version'] for c in cells}
        systems={json.loads(c['request_body'])['messages'][0]['content'] for c in cells}
        if len(material_hashes)!=1 or len(none_hashes)!=1 or len(versions)!=1 or len(systems)!=1:
            return jsonify(error='准备期间资料或参数发生变化，未冻结本轮；请重新准备。'),409
        for sid in (11,12,13):
            if len({c['sha256'] for c in cells if c['student_id']==sid and c['mode']=='profile'})!=1:
                return jsonify(error='准备期间画像发生变化，未冻结本轮；请重新准备。'),409
        identity=uuid4().hex
        manifest=dict(version='lecture-comparison-v2',design='三类虚拟画像 × 有/无画像 × 两次重复，共12次。固定目标、SQL材料、授课要求与模型参数。',
            material_sha256=next(iter(material_hashes)),prompt_version=next(iter(versions)),model=requested_model,
            rubric=['内容正确性','目标覆盖','诊断与讲解对应','引用支持'],
            note='逐份从讲义的编辑与审核记录问题、检查表和实际用时。助手草稿不代表教师审核；零秒表示未记录。')
        with db() as conn:
            conn.execute('INSERT INTO lecture_experiments(id,manifest) VALUES (%s,%s)',(identity,Jsonb(manifest)))
            for cell in cells:
                conn.execute('INSERT INTO lecture_experiment_cells(experiment_id,cell,snapshot) VALUES (%s,%s,%s)',(identity,cell['cell'],Jsonb(cell)))
        return jsonify(detail(identity)),201

    def work(identity):
        try:
            with db() as conn:
                cells=conn.execute("SELECT * FROM lecture_experiment_cells WHERE experiment_id=%s AND status='prepared' ORDER BY cell",(identity,)).fetchall()
            for row in cells:
                cell=row['snapshot'];key=(identity,row['cell'])
                body=cell['request_body']
                if hashlib.sha256(body.encode()).hexdigest()!=cell['sha256']:
                    with db() as conn:conn.execute("UPDATE lecture_experiment_cells SET status='failed',error='冻结输入校验不一致，未调用模型。' WHERE experiment_id=%s AND cell=%s",key)
                    continue
                with db() as conn:conn.execute("UPDATE lecture_experiment_cells SET status='dispatching' WHERE experiment_id=%s AND cell=%s",key)
                try:
                    job=queue().submit(cell['student_id'],cell['topic'],cell['goal'],cell['context'],PreparedRequest(body,cell['sha256'],cell['prompt_version'],cell['input_version']))
                    with db() as conn:conn.execute("UPDATE lecture_experiment_cells SET status='queued',job_id=%s WHERE experiment_id=%s AND cell=%s",(job['id'],*key))
                    while not queue().wait_idle(10):pass
                    job=queue().detail(job['id'])
                    status='succeeded' if job['status']=='succeeded' else 'failed'
                    with db() as conn:conn.execute('UPDATE lecture_experiment_cells SET status=%s,error=%s WHERE experiment_id=%s AND cell=%s',(status,job.get('error_message') or '',*key))
                except Exception:
                    with db() as conn:conn.execute("UPDATE lecture_experiment_cells SET status='interrupted',error='提交或结果确认异常，请核查生成记录；不自动重发。' WHERE experiment_id=%s AND cell=%s",key)
                    raise
            with db() as conn:conn.execute("UPDATE lecture_experiments SET status='completed' WHERE id=%s",(identity,))
        except Exception:
            try:
                with db() as conn:
                    conn.execute("UPDATE lecture_experiments SET status='interrupted' WHERE id=%s",(identity,))
            except Exception:
                app.logger.error('Could not finalize fixed comparison %s.', identity)
        finally:gate.release()

    @app.post('/api/lecture-experiments/<identity>/run')
    def run(identity):
        if not gate.acquire(blocking=False):return jsonify(error='已有固定对照正在执行。'),409
        try:
            current=detail(identity)
            if not current:gate.release();return jsonify(error='对照不存在。'),404
            if not queue().wait_idle(0) or not other_tasks_idle():
                gate.release();return jsonify(error='请等待讲义、出题或阅卷任务结束。'),409
            if not any(c['status']=='prepared' for c in current['cells']):
                gate.release();return jsonify(error='本轮没有尚未运行的格子；已有成功、失败和中断均保留。'),409
            with db() as conn:conn.execute("UPDATE lecture_experiments SET status='running' WHERE id=%s",(identity,))
            Thread(target=work,args=(identity,),daemon=True,name='lecture-comparison').start()
        except Exception:
            if gate.locked():gate.release()
            raise
        return jsonify(id=identity,status='running'),202

    @app.get('/api/lecture-experiments')
    def history():
        with db() as conn:rows=conn.execute('SELECT id,created_at,status,manifest FROM lecture_experiments ORDER BY created_at DESC LIMIT 50').fetchall()
        return jsonify(experiments=rows)

    @app.get('/api/lecture-experiments/<identity>')
    def show(identity):
        result=detail(identity)
        return jsonify(result) if result else (jsonify(error='对照不存在。'),404)
    return recover
