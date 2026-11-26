"""The citation judge (demo/scale/judge_citations.py) when the model answers only in part; never calls a model."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scale'))
import judge_citations  # noqa: E402
from qwen import ModelError, ModelResult  # noqa: E402

CLAIMS = [dict(id=f'C{n}', line=n, text=f'第 {n} 行', before='', cites=['M1']) for n in range(1, 6)]
MATERIALS = [dict(id=1, content='材料原文')]


def model(longest):
    """A model that runs past max_tokens when asked about more than longest claims at once."""
    asked = []

    def call(prepared, on_attempt):
        claims = json.loads(json.loads(prepared.body)['messages'][1]['content'])['claims']
        asked.append([claim['id'] for claim in claims])
        if len(claims) > longest:
            raise ModelError('truncated', '模型输出达到长度上限', 'output', ModelResult('', {'total_tokens': 100}))
        return ModelResult(json.dumps(dict(judgments=[dict(id=claim['id'], verdict='supported', reason='有依据')
                                                      for claim in claims])), {'total_tokens': 10})
    return call, asked


class JudgeTests(unittest.TestCase):
    def test_an_answer_cut_off_at_the_length_limit_is_asked_again_in_two_halves(self):
        call, asked = model(longest=3)
        with patch('qwen.call_qwen', call):
            verdicts, usage = judge_citations.judge('m', CLAIMS, MATERIALS, lambda text: None)
        self.assertEqual(asked, [['C1', 'C2', 'C3', 'C4', 'C5'], ['C1', 'C2', 'C3'], ['C4', 'C5']])
        self.assertEqual(set(verdicts), {claim['id'] for claim in CLAIMS})
        self.assertEqual(usage, 120)   # the cut-off answer's tokens are counted too

    def test_claims_still_cut_off_stay_unjudged_instead_of_guessed(self):
        call, asked = model(longest=1)
        logged = []
        with patch('qwen.call_qwen', call):
            verdicts, _ = judge_citations.judge('m', CLAIMS[:2], MATERIALS, logged.append)
        self.assertEqual(set(verdicts), {'C1', 'C2'})   # halves of one claim each fit
        call, asked = model(longest=0)
        with patch('qwen.call_qwen', call):
            verdicts, usage = judge_citations.judge('m', CLAIMS[:2], MATERIALS, logged.append)
        self.assertEqual(verdicts, {})
        self.assertEqual(len(asked), 6)   # two attempts, each the whole batch and then its two halves
        self.assertEqual(usage, 600)
        self.assertTrue(any('记为未判断' in text for text in logged))


if __name__ == '__main__':
    unittest.main()
