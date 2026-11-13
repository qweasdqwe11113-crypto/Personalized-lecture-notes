"""Expanded output budgets, instructions and frozen legacy length rules."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prompts import prepare_request
from teaching import DEFAULTS, length_range


class LectureLengthTests(unittest.TestCase):
    def test_new_policy_overrides_short_template_and_keeps_legacy_request(self):
        legacy = {'topic': {'custom': True}, 'materials': []}
        ctx = {**legacy, 'lecture_length_policy': 'expanded-v1'}
        before = copy.deepcopy(ctx)
        prepared = prepare_request(ctx, '课堂目标', 'fixture')
        request = json.loads(prepared.body)
        self.assertEqual(request['max_tokens'], 16000)
        self.assertIn('4000—6000', request['messages'][0]['content'])
        self.assertIn('8000', request['messages'][0]['content'])
        self.assertTrue(prepared.prompt_version.endswith('+expanded-v1'))
        self.assertEqual(ctx, before)
        old = json.loads(prepare_request(legacy, '课堂目标', 'fixture').body)
        self.assertEqual(old['max_tokens'], 2600)
        self.assertNotIn('expanded-v1', old['messages'][0]['content'])

    def test_detail_levels_have_room_for_worked_examples_and_cap_at_8000(self):
        for detail, expected in [('brief', (1500, 2500)), ('standard', (4000, 6000)), ('detailed', (6000, 8000))]:
            options = {**DEFAULTS, 'detail': detail}
            self.assertEqual(length_range(options, True), expected)
            ctx = {'topic': {'custom': True}, 'materials': [], 'lecture_length_policy': 'expanded-v1', 'teaching_options': options}
            payload = json.loads(prepare_request(ctx, '目标', 'fixture').body)
            self.assertEqual(payload['max_tokens'], 16000)
            self.assertIn(f'{expected[0]}—{expected[1]}', payload['messages'][0]['content'])
        self.assertEqual(length_range({**DEFAULTS, 'detail': 'detailed', 'examples': 6, 'exercises': 10}, True), (6000, 8000))
        self.assertEqual(length_range(DEFAULTS), (900, 1800))
