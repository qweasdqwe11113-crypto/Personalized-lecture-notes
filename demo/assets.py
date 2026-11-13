"""Image assets: extraction, deterministic normalization and byte-exact fingerprints.

The bytes a model sees are fixed once, when an image enters the library. Nothing
downstream may re-encode it: a different Pillow or JPEG encoder version produces
different bytes, and then saved request bodies no longer match their SHA-256.

Normalization is a fixed ladder: the first step whose output fits the byte budget
wins. Same input always yields the same step, the same bytes and the same digest.
"""
from dataclasses import dataclass
import base64
import hashlib
import io
from pathlib import Path
from zipfile import ZipFile

from PIL import Image, ImageOps

MIME = 'image/jpeg'
MAX_BYTES = 1258291
NORMALIZE_VERSION = 'jpeg-ladder-v1'
# (max_edge, quality); tried in order, never guessed at request time.
LADDER = ((1024, 85), (896, 82), (768, 80), (640, 78))
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'}


class AssetError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    """One image as it was found, before any processing."""
    raw: bytes
    label: str
    page_no: int = None


@dataclass(frozen=True)
class Asset:
    """One image after normalization; `data` is exactly what the model is shown."""
    data: bytes
    source_sha256: str
    normalized_sha256: str
    width: int
    height: int
    step: int

    @property
    def byte_size(self):
        return len(self.data)

    def data_uri(self):
        # Read the stored bytes verbatim; re-encoding here would break the digest.
        return f'data:{MIME};base64,' + base64.b64encode(self.data).decode('ascii')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def normalize(raw):
    """Encode once through a fixed ladder. Returns (bytes, width, height, step)."""
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened)
            if image.mode != 'RGB':
                image = image.convert('RGB')
            width, height = image.size
            base = image.copy()
    except AssetError:
        raise
    except Exception as exc:
        raise AssetError('图片无法解析，请确认文件是完整图片。') from exc
    for step, (max_edge, quality) in enumerate(LADDER):
        scale = min(1.0, max_edge / max(width, height))
        candidate = base
        if scale < 1.0:
            candidate = base.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.LANCZOS)
        buffer = io.BytesIO()
        candidate.save(buffer, format='JPEG', quality=quality, optimize=False, progressive=False)
        data = buffer.getvalue()
        if len(data) <= MAX_BYTES:
            return data, candidate.width, candidate.height, step
    raise AssetError('图片压缩后仍超过体积上限，请换一张更小的图。')


def build(raw, label=''):
    data, width, height, step = normalize(raw)
    return Asset(data, sha256(raw), sha256(data), width, height, step)


def extract(raw, filename, limit=None):
    """Pull images out of a PDF, a DOCX or a plain image file."""
    ext = Path(filename).suffix.lower()
    if ext == '.pdf':
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            if reader.is_encrypted:
                raise AssetError('请先解密 PDF，再导入。')
            found = []
            for number, page in enumerate(reader.pages, 1):
                try:
                    for image in page.images:
                        found.append(Source(image.data, image.name, number))
                        if limit is not None and len(found) >= limit:
                            return found
                except Exception:
                    continue  # A page with an undecodable image stream still yields the others.
            return found
        except AssetError:
            raise
        except Exception as exc:
            raise AssetError('PDF 解析失败，请确认文件完整。') from exc
    if ext == '.docx':
        try:
            with ZipFile(io.BytesIO(raw)) as archive:
                names = sorted(name for name in archive.namelist() if name.startswith('word/media/'))
                return [Source(archive.read(name), Path(name).name) for name in (names[:limit] if limit else names)]
        except Exception as exc:
            raise AssetError('DOCX 解析失败，请确认文件完整。') from exc
    if ext not in EXTENSIONS:
        raise AssetError('支持 PNG、JPEG、WebP、BMP、GIF、TIFF 图片，或 PDF、DOCX 中的插图。')
    return [Source(raw, Path(filename).name)]


def save(conn, asset, *, material_id=None, upload_id=None, kind='figure', label='', page_no=None):
    """Insert one asset, or return the existing row when the bytes are already known."""
    row = conn.execute('''INSERT INTO material_assets
        (material_id,upload_id,kind,label,page_no,source_sha256,normalized_sha256,mime,
         width,height,byte_size,normalize_version,bytes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (normalized_sha256) DO NOTHING RETURNING id''',
        (material_id, upload_id, kind, label[:200], page_no, asset.source_sha256,
         asset.normalized_sha256, MIME, asset.width, asset.height, asset.byte_size,
         NORMALIZE_VERSION, asset.data)).fetchone()
    if row is None:
        row = conn.execute('SELECT id FROM material_assets WHERE normalized_sha256=%s',
                           (asset.normalized_sha256,)).fetchone()
    return row['id']


def attach(conn, run_id, asset_ids):
    for ordinal, (asset_id, digest, size) in enumerate(asset_ids, 1):
        conn.execute('''INSERT INTO generation_run_assets (run_id,asset_id,ordinal,normalized_sha256,byte_size)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT (run_id,ordinal) DO NOTHING''',
                     (run_id, asset_id, ordinal, digest, size))


def for_run(conn, run_id):
    return conn.execute('''SELECT a.id,a.label,a.page_no,a.width,a.height,a.byte_size,a.normalized_sha256,r.ordinal
        FROM generation_run_assets r JOIN material_assets a ON a.id=r.asset_id
        WHERE r.run_id=%s ORDER BY r.ordinal''', (run_id,)).fetchall()
