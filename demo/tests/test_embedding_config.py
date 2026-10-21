"""Embedding configuration must remain independent of generation credentials."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from embed import QwenEmbedder


class EmbeddingConfigTests(unittest.TestCase):
    def test_separate_endpoint_and_key(self):
        with patch.dict(os.environ, {
            'QWEN_BASE_URL': 'https://generation.invalid/v1',
            'QWEN_API_KEY': 'generation-key',
            'QWEN_MODEL': 'generation-model',
            'QWEN_EMBEDDING_BASE_URL': 'http://127.0.0.1:11434/v1/',
            'QWEN_EMBEDDING_API_KEY': 'local-key',
            'QWEN_EMBEDDING_MODEL': 'bge-m3:latest',
        }, clear=True):
            client = QwenEmbedder()
            self.assertEqual(client.base, 'http://127.0.0.1:11434/v1')
            self.assertEqual(client._key, 'local-key')
            self.assertEqual(client.model, 'bge-m3:latest')
            explicit = QwenEmbedder(base='http://explicit.invalid/v1', key='explicit')
            self.assertEqual(explicit.base, 'http://explicit.invalid/v1')
            self.assertEqual(explicit._key, 'explicit')

    def test_blank_overrides_use_generation_config(self):
        with patch.dict(os.environ, {
            'QWEN_BASE_URL': 'https://generation.invalid/v1',
            'QWEN_API_KEY': 'generation-key',
            'QWEN_MODEL': 'generation-model',
            'QWEN_EMBEDDING_BASE_URL': '',
            'QWEN_EMBEDDING_API_KEY': '',
            'QWEN_EMBEDDING_MODEL': '',
        }, clear=True):
            client = QwenEmbedder()
            self.assertEqual(client.base, 'https://generation.invalid/v1')
            self.assertEqual(client._key, 'generation-key')
            self.assertEqual(client.model, 'generation-model')
