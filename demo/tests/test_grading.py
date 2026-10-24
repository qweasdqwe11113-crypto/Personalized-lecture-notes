"""Grading extraction, score validation and isolated persistent task workflows."""
from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
from threading import Event, Lock
import time
import unittest
from unittest.mock import patch

from flask import Flask
from PIL import Image
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grading
from qwen import ModelResult


def result_fixture():
    return {'questions': [dict(number='1', stem='2+3=?', student_answer='6', reference_answer='5',
        basis='provided', max_score=10, score=0, status='graded', feedback='加法计算错误，2+3=5。'),
        dict(number='2', stem='解方程 x+2=5', student_answer='【无法确认】', reference_answer='x=3',
        basis='inferred', max_score=10, score=None, status='needs_review', feedback='作答无法识读，请核对原卷。')]}


class GradingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.testing = True
        self.requests = []
        self.recover = grading.register_grading(self.app, self.directory.name, self.model, Lock())
        self.client = self.app.test_client()

    def tearDown(self):
        # Every accepted task is awaited before temporary storage is removed.
        self.directory.cleanup()

    def model(self, prepared, attempt):
        self.requests.append(json.loads(prepared.body))
        attempt()
        return ModelResult(json.dumps(result_fixture(), ensure_ascii=False))

    def submit(self, **kwargs):
        data = {'paper': (BytesIO(b'1. 2+3=? student:6\n2. x+2=5 student:unclear'), 'paper.txt'),
                'reference': (BytesIO(b'1. 5'), 'answers.txt'), 'requirements': 'step marks'}
        data.update(kwargs)
        response = self.client.post('/api/grading', data=data)
        self.assertEqual(response.status_code, 202, response.json)
        return response.json['id']

    def wait(self, identity):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = self.client.get('/api/grading/' + identity).json
            if row['status'] in ('completed', 'failed'):
                # State is committed just before the worker releases admission.
                time.sleep(.02)
                return row
            time.sleep(.01)
        self.fail('task did not complete')

    def test_upload_grade_history_download_and_frozen_input(self):
        identity = self.submit()
        row = self.wait(identity)
        self.assertEqual(row['status'], 'completed', row)
        self.assertEqual(row['result']['pending'], 1)
        self.assertEqual(row['result']['score'], 0)
        self.assertEqual(row['result']['max_score'], 20)
        payload = json.loads(self.requests[0]['messages'][1]['content'])
        self.assertIn('student:6', payload['paper'])
        self.assertEqual(payload['reference'], '1. 5')
        self.assertEqual(payload['teacher_requirements'], 'step marks')
        self.assertEqual(len(self.client.get('/api/grading').json['records']), 1)
        exported = self.client.get('/api/grading/' + identity + '/download')
        self.assertEqual(exported.status_code, 200)
        self.assertIn('待核对', exported.data.decode())
        self.assertNotIn('request_body', row)

    def test_invalid_file_missing_file_and_unknown_record(self):
        self.assertEqual(self.client.post('/api/grading', data={}).status_code, 400)
        response = self.client.post('/api/grading', data={'paper': (BytesIO(b'a'), 'paper.exe')})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/grading/missing').status_code, 404)
        self.assertEqual(self.client.get('/api/grading/missing/download').status_code, 404)

    def test_admission_busy_and_model_failure(self):
        entered, release = Event(), Event()
        def blocked(raw, filename):
            entered.set(); release.wait(3)
            raise ValueError('识读失败')
        with patch('grading.read_document', blocked):
            identity = self.submit()
            self.assertTrue(entered.wait(2))
            response = self.client.post('/api/grading', data={'paper': (BytesIO(b'x'), 'second.txt')})
            self.assertEqual(response.status_code, 409)
            release.set()
            row = self.wait(identity)
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['error'], '识读失败')
        self.assertEqual(self.wait(self.submit())['status'], 'completed')

    def test_invalid_and_uncertain_scores_cannot_be_saved(self):
        for change in ({'score':11}, {'score':True}, {'score':float('nan')}, {'max_score':0},
                       {'status':'needs_review','score':1}, {'basis':'unknown'}, {'reference_answer':''}):
            data = result_fixture(); data['questions'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                grading.parse_result(json.dumps(data))
        data = result_fixture(); data['questions'][1]['number'] = '1'
        with self.assertRaises(ValueError): grading.parse_result(json.dumps(data))
        with self.assertRaises(ValueError): grading.parse_result('{"error":"超过30题"}')

    def test_pdf_renders_whole_pages_and_images_use_grading_prompt(self):
        writer = PdfWriter(); writer.add_blank_page(width=300,height=400)
        raw = BytesIO(); writer.write(raw)
        with patch('grading.analyse', return_value={'content':'problem and handwritten answer'}) as call:
            text = grading.read_document(raw.getvalue(), 'scan.pdf')
            self.assertIn('problem and handwritten answer', text)
            self.assertEqual(call.call_count, 1)
            self.assertIn('学生手写作答', call.call_args.kwargs['prompt'])
        image = BytesIO(); Image.new('RGB',(100,100),'white').save(image, format='PNG')
        with patch('grading.analyse', return_value={'content':'2+3=6'}):
            self.assertIn('2+3=6', grading.read_document(image.getvalue(), 'photo.png'))
        for _ in range(8): writer.add_blank_page(width=300,height=400)
        raw = BytesIO(); writer.write(raw)
        with self.assertRaises(ValueError): grading.read_document(raw.getvalue(),'long.pdf')

    def test_restart_marks_incomplete_records(self):
        store = Path(self.directory.name)/'.runtime'/'grading.sqlite3'
        # Initialize storage without a running worker.
        self.client.get('/api/grading')
        import sqlite3
        with closing(sqlite3.connect(store)) as conn, conn:
            conn.execute("INSERT INTO grading_jobs(id,filename,created_at,status) VALUES ('orphan','paper.txt','now','grading')")
        self.recover()
        self.assertEqual(self.client.get('/api/grading/orphan').json['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
