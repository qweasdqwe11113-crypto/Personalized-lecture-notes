"""Image asset checks: deterministic bytes and storage, with no model traffic.

Run: .venv/Scripts/python -m unittest discover -s demo/tests -v
Bytes are produced by Pillow only; nothing here calls a generation or embedding service.
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import assets
from test_profiles import ProfileDatabaseFixture

DEMO = Path(__file__).resolve().parents[1]
SCHEMA = (DEMO / 'schema.sql').read_text()


def png(width, height, color=(30, 90, 160)):
    buffer = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buffer, format='PNG')
    return buffer.getvalue()


class NormalizeTests(unittest.TestCase):
    def test_same_input_gives_same_bytes_and_digest(self):
        first, second = assets.build(png(400, 300)), assets.build(png(400, 300))
        self.assertEqual(first.data, second.data)
        self.assertEqual(first.normalized_sha256, second.normalized_sha256)
        self.assertNotEqual(first.source_sha256, first.normalized_sha256)

    def test_digest_matches_the_bytes_that_would_be_sent(self):
        asset = assets.build(png(400, 300))
        self.assertEqual(hashlib.sha256(asset.data).hexdigest(), asset.normalized_sha256)

    def test_oversized_image_is_scaled_to_the_first_ladder_step(self):
        asset = assets.build(png(4000, 3000))
        self.assertEqual(max(asset.width, asset.height), assets.LADDER[0][0])
        self.assertLessEqual(asset.byte_size, assets.MAX_BYTES)

    def test_small_image_is_never_upscaled(self):
        asset = assets.build(png(120, 80))
        self.assertEqual((asset.width, asset.height), (120, 80))

    def test_different_content_gives_different_digest(self):
        dark = assets.build(png(200, 200, (0, 0, 0))).normalized_sha256
        light = assets.build(png(200, 200, (255, 255, 255))).normalized_sha256
        self.assertNotEqual(dark, light)

    def test_data_uri_carries_exactly_the_stored_bytes(self):
        asset = assets.build(png(300, 200))
        prefix = 'data:' + assets.MIME + ';base64,'
        uri = asset.data_uri()
        self.assertTrue(uri.startswith(prefix))
        self.assertEqual(base64.b64decode(uri[len(prefix):]), asset.data)

    def test_broken_bytes_are_rejected(self):
        with self.assertRaises(assets.AssetError):
            assets.build(b'not an image at all')


class ExtractTests(unittest.TestCase):
    def test_plain_image_file_yields_one_source(self):
        sources = assets.extract(png(50, 50), 'figure.png')
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].label, 'figure.png')

    def test_unsupported_extension_is_rejected(self):
        with self.assertRaises(assets.AssetError):
            assets.extract(b'anything', 'notes.txt')

    def test_pdf_embedded_images_are_found(self):
        buffer = io.BytesIO()
        Image.new('RGB', (600, 400), (200, 30, 30)).save(buffer, format='PDF')
        sources = assets.extract(buffer.getvalue(), 'chapter.pdf')
        self.assertTrue(sources, 'PDF 中的插图应被抽出')
        asset = assets.build(sources[0].raw)
        self.assertEqual(asset.width, 600)
        self.assertEqual(sources[0].page_no, 1)


class AssetStorageTests(ProfileDatabaseFixture):
    def setUp(self):
        super().setUp()
        # The fixture only builds the pre-migration schema; apply the rest so the
        # asset tables exist exactly as they do in the real database.
        remainder = SCHEMA.split('-- Additive migration: ', 1)[1].split('\n', 1)[1]
        with self.fixture_db() as conn:
            conn.execute(remainder)

    def material(self, conn):
        return conn.execute('SELECT min(id) AS id FROM materials').fetchone()['id']

    def test_same_bytes_are_stored_once(self):
        with self.fixture_db() as conn:
            material_id = self.material(conn)
            first = assets.save(conn, assets.build(png(300, 200)), material_id=material_id, label='图一')
            second = assets.save(conn, assets.build(png(300, 200)), material_id=material_id, label='图一')
            self.assertEqual(first, second)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM material_assets').fetchone()['n'], 1)

    def test_stored_bytes_match_their_digest(self):
        with self.fixture_db() as conn:
            asset = assets.build(png(320, 240))
            asset_id = assets.save(conn, asset, material_id=self.material(conn))
            row = conn.execute('SELECT bytes,normalized_sha256 FROM material_assets WHERE id=%s',
                               (asset_id,)).fetchone()
            stored = bytes(row['bytes'])
            self.assertEqual(stored, asset.data)
            self.assertEqual(hashlib.sha256(stored).hexdigest(), row['normalized_sha256'])

    def test_request_body_digest_is_reproducible_from_stored_bytes(self):
        """The promise the whole layer exists for: rebuild the body later, same SHA-256."""
        with self.fixture_db() as conn:
            asset = assets.build(png(400, 300))
            asset_id = assets.save(conn, asset, material_id=self.material(conn))
            row = conn.execute('SELECT bytes FROM material_assets WHERE id=%s', (asset_id,)).fetchone()

        def body(uri):
            return json.dumps({'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': '请讲解这张图'},
                {'type': 'image_url', 'image_url': {'url': uri}}]}]},
                ensure_ascii=False, separators=(',', ':'))

        def digest(text):
            return hashlib.sha256(text.encode('utf-8')).hexdigest()

        stored = bytes(row['bytes'])
        rebuilt = 'data:' + assets.MIME + ';base64,' + base64.b64encode(stored).decode('ascii')
        self.assertEqual(digest(body(rebuilt)), digest(body(asset.data_uri())))

    def test_different_images_are_kept_apart(self):
        with self.fixture_db() as conn:
            material_id = self.material(conn)
            assets.save(conn, assets.build(png(300, 200, (10, 10, 10))), material_id=material_id)
            assets.save(conn, assets.build(png(300, 200, (200, 200, 200))), material_id=material_id)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM material_assets').fetchone()['n'], 2)


if __name__ == '__main__':
    unittest.main()
