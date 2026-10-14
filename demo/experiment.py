"""Frozen, resumable small experiment. No image/vector data or fabricated output."""
import argparse
import copy
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
from pathlib import Path

import app
from prompts import PreparedRequest, prepare_request


def write_json(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    temp.replace(path)


def build_manifest():
    topic = 'probability_advanced'
    goal = app.TOPICS[topic]['goal']
    cells = []
    for repeat in [1, 2]:
        for sid in [11, 12, 13]:
            for mode in ['profile', 'none']:
                context = app.get_context(sid, topic, mode)
                prepared = prepare_request(context, goal, app.MODEL)
                cells.append({'cell': len(cells) + 1, 'student_id': sid, 'mode': mode,
                              'repeat': repeat, 'topic': topic, 'goal': goal, 'context': context,
                              'request_body': prepared.body, 'sha256': prepared.sha256,
                              'prompt_version': prepared.prompt_version, 'input_version': prepared.input_version})
    baseline = cells[1]['context']['materials']
    assert all(cell['context']['materials'] == baseline for cell in cells)
    none_hashes = {cell['sha256'] for cell in cells if cell['mode'] == 'none'}
    assert len(none_hashes) == 1  # No learner names/IDs/diagnoses leaked to ablation.
    for sid in [11, 12, 13]:
        assert len({c['sha256'] for c in cells if c['student_id'] == sid and c['mode'] == 'profile'}) == 1
    payload = json.loads(cells[0]['request_body'])
    return {'experiment_version': 'bayes-paired-v1',
            'prepared_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
            'design': '3 类虚拟画像 × 有/无画像 × 2 次；12 次独立调用；同一共同目标、材料、模板和参数',
            'parameters': {k: v for k, v in payload.items() if k != 'messages'},
            'materials_sha256': hashlib.sha256(json.dumps(baseline, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            'rubric': {'mathematics': '公式、条件及固定例题核算', 'goal': '推导、两例、敏感性与归一',
                       'personalization': '是否对应诊断；无画像是否避免猜测', 'citations': '逐处内容与材料支持',
                       'workload': '记录实际修订行数及自报审核用时；未记录保持空缺'},
            'note': '重复输入不保证确定性。助手核查与教师评审分开，虚拟学生不支持真实学习效果结论。',
            'cells': cells}


def run_manifest(directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest['experiment_version'] != 'bayes-paired-v1':
        raise ValueError('Unsupported experiment version.')
    path = directory / 'results.json'
    results = json.loads(path.read_text()) if path.exists() else []
    (directory / 'outputs').mkdir(exist_ok=True)
    for cell in manifest['cells']:
        if any(item['cell'] == cell['cell'] for item in results):
            continue  # Failed calls are recorded, never silently replaced.
        body = cell['request_body']
        if hashlib.sha256(body.encode()).hexdigest() != cell['sha256']:
            raise ValueError('Manifest input integrity mismatch.')
        prepared = PreparedRequest(body, cell['sha256'], cell['prompt_version'], cell['input_version'])
        app.GENERATION_LOCK.acquire()
        data, status = app.generate_locked(cell['student_id'], cell['topic'], cell['goal'], cell['mode'],
                                           copy.deepcopy(cell['context']), prepared)
        item = {k: cell[k] for k in ['cell', 'student_id', 'mode', 'repeat', 'sha256']}
        item.update(http_status=status, generation_run_id=data.get('generation_run_id'),
                    lecture_id=data.get('id'), elapsed_seconds=data.get('elapsed_seconds'),
                    usage=data.get('usage'), error=data.get('error'))
        if status == 200:
            item['quality_report'] = data['citation_check']['quality_report']
            file = f'outputs/cell-{cell["cell"]:02}-lecture-{data["id"]}.md'
            (directory / file).write_text(data['content'])
            item['output_file'] = file
        results.append(item)
        write_json(path, results)
        print(json.dumps({k: item[k] for k in ['cell','student_id','mode','repeat','generation_run_id','lecture_id','http_status','elapsed_seconds']}, ensure_ascii=False), flush=True)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'run'])
    parser.add_argument('--directory', type=Path, default=Path('experiments/2026-10-12-bayes'))
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    if args.action == 'prepare':
        target = args.directory / 'manifest.json'
        if target.exists():
            raise SystemExit('实验清单已存在；不覆盖冻结输入。请使用新目录准备另一轮。')
        write_json(target, build_manifest())
        print('已冻结 12 份实际输入；尚未调用模型。')
    else:
        run_manifest(args.directory)
