"""A one-student request never carries the student's name to the model service.

Pure request assembly: no database and no model. The frozen context keeps the name (it stays on this
machine); every request body built from it says 「该生」 instead, outside the course material.
"""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import designed  # noqa: E402
from presentations import prepare_slide_edit, prepare_slides  # noqa: E402
from prompts import ANONYMOUS, learner_name, prepare_request, without_name  # noqa: E402
from quizzes import prepare_quiz  # noqa: E402
from sections import prepare_section  # noqa: E402

NAME = '许嘉禾'
OPTIONS = {'duration': 40, 'lesson_type': 'new', 'detail': 'brief', 'length_policy': 'duration-v2', 'examples': 2, 'exercises': 3}


def personal(**more):
    """A one-student context with the name in every learner field, and once inside a course problem."""
    return {
        'topic': {'title': '等式的性质', 'goal': f'帮{NAME}分清两条性质', 'knowledge_ids': [1],
                  'knowledge_names': ['等式的性质'], 'lesson_plan': f'{NAME}上次移项忘了变号', 'custom': True},
        'personalization_mode': 'profile',
        'student': {'id': 7, 'name': NAME, 'subtitle': f'{NAME} · 初一', 'profile': f'{NAME}计算细心，移项常忘变号。'},
        'states': [{'id': 1, 'name': '等式的性质', 'state': 'partial', 'evidence': f'{NAME}的作业 3',
                    'recent_evidence': [{'source': '作业', 'task_content': '2x+3=7', 'performance': f'{NAME}写成 2x=7+3'}]}],
        'personalization_plan': [{'knowledge_id': 1, 'name': '等式的性质', 'state': 'partial',
                                  'action': f'先让{NAME}口述每一步依据', 'warnings': [f'{NAME}只有 1 条证据']}],
        'learner_note': f'{NAME}今天刚请假回来',
        'materials': [{'id': 5, 'title': '课本例题', 'knowledge_id': 1, 'content': f'{NAME}买了 3 支笔，共 12 元。'}],
        'teaching_options': dict(OPTIONS),
        **more,
    }


def user_of(body):
    return json.loads(json.loads(body)['messages'][1]['content'])


class NameIsNotSent(unittest.TestCase):
    def assertAnonymous(self, user, materials=('materials', 'sources')):
        """No name outside the course material; the material itself is left as written."""
        def strip(value):
            if isinstance(value, dict):
                return {k: strip(v) for k, v in value.items() if k not in materials}
            if isinstance(value, list):
                return [strip(v) for v in value]
            return value
        self.assertNotIn(NAME, json.dumps(strip(user), ensure_ascii=False))
        self.assertIn(ANONYMOUS, json.dumps(strip(user), ensure_ascii=False))

    def test_the_designed_lesson_request_says_该生(self):
        context = personal(generation_mode='designed-v1')
        frozen = copy.deepcopy(context)
        user = user_of(prepare_request(context, f'让{NAME}会用性质解方程', 'm').body)
        self.assertAnonymous(user)
        self.assertEqual(user['context']['student']['name'], ANONYMOUS)
        self.assertEqual(user['context']['learner_note'], '该生今天刚请假回来')
        self.assertEqual(user['context']['materials'][0]['content'], f'{NAME}买了 3 支笔，共 12 元。')
        self.assertEqual(context, frozen, 'the frozen context keeps the name')

    def test_the_older_writing_modes_say_该生_too(self):
        for mode in ('single', 'sectioned-v1'):
            with self.subTest(mode=mode):
                user = user_of(prepare_request(personal(generation_mode=mode), '目标', 'm').body)
                self.assertAnonymous(user)
                self.assertEqual(user['context']['materials'][0]['content'], f'{NAME}买了 3 支笔，共 12 元。')

    def test_segment_and_repair_requests_scrub_a_name_the_teacher_typed_into_the_design(self):
        context = personal(generation_mode='designed-v1', designed={'segment_system': '写一个环节'})
        frozen = prepare_request(context, '目标', 'm')
        design = {'segments': [{'id': 'S1', 'title': '导入', 'minutes': 5, 'sources': [], 'items': [], 'knowledge_ids': [1],
                                'teacher': f'问{NAME}上次错在哪'}], 'items': [], 'objectives': []}
        request = designed.segment_request(frozen.body, context, design, design['segments'][0], {'low': 100, 'high': 200})
        self.assertAnonymous(user_of(request.body))
        repair = designed.repair_request(request, f'{NAME}先说', [f'环节 S1 写了「{NAME}」'], 'v', learner_name(context))
        self.assertNotIn(NAME, repair.body.replace(f'{NAME}买了', ''))  # the course problem keeps its 许嘉禾

    def test_companion_quizzes_section_edits_and_slides_say_该生(self):
        lecture = f'## 学情\n{NAME}容易忘记变号。\n\n## 例题\n解 2x+3=7。'
        quiz = {**personal(), 'lecture': {'id': 1, 'revision': 0, 'content': lecture, 'personalization_plan': []},
                'question_plan': [{'number': 1, 'type': 'short', 'difficulty': 'basic', 'knowledge_id': 1}], 'json_mode': True}
        self.assertAnonymous(user_of(prepare_quiz(quiz, {'count': 1}, 'm').body))
        edit = dict(base_content=lecture, title='学情', action='expand', instruction=f'多写{NAME}的情况',
                    index=0, start=0, end=10, sha256='x')
        self.assertAnonymous(user_of(prepare_section({**personal(generation_mode='single'), 'section_edit': edit},
                                                     '目标', 'm').body))
        export = dict(base_content=lecture, goal='目标', slide_count=6, instruction=f'封面不写{NAME}')
        slides = prepare_slides({**personal(), 'ppt_export': export}, 'm')
        self.assertNotIn(NAME, json.loads(slides.body)['messages'][1]['content'].replace(f'{NAME}买了', ''))
        deck = {'title': '等式', 'slides': [{'title': '封面', 'kind': 'cover', 'notes': f'向{NAME}问好'}]}
        page = prepare_slide_edit({**personal(), 'ppt_export': export,
                                   'ppt_slide_edit': dict(page=1, base_deck=deck, instruction='')}, 'm')
        self.assertNotIn(NAME, json.loads(page.body)['messages'][1]['content'].replace(f'{NAME}买了', ''))

    def test_class_and_unprofiled_lessons_are_unchanged(self):
        klass = personal(generation_mode='designed-v1', personalization_mode='class',
                         student={'name': '初一（2）班（全班）', 'profile': '班级特点'})
        self.assertIsNone(learner_name(klass))
        self.assertEqual(user_of(prepare_request(klass, '目标', 'm').body)['context']['student']['name'], '初一（2）班（全班）')
        self.assertIsNone(learner_name({'personalization_mode': 'none', 'student': {'name': '通用讲义'}}))
        self.assertEqual(without_name({'a': NAME}, None), {'a': NAME})

    def test_a_one_letter_name_is_replaced_only_where_it_is_the_whole_value(self):
        self.assertEqual(without_name({'student': {'name': '王'}, 'note': '王老师'}, '王'),
                         {'student': {'name': ANONYMOUS}, 'note': '王老师'})


if __name__ == '__main__':
    unittest.main()
