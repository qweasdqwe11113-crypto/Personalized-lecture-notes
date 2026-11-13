"""Local document library and bounded public-web imports, independent of model calls."""
from contextlib import closing
from datetime import datetime, timezone
from html.parser import HTMLParser
from io import BytesIO
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import sqlite3
import ssl
import unicodedata
import uuid
from urllib.parse import urljoin, urlsplit, unquote, quote
from zipfile import ZipFile, BadZipFile

from flask import jsonify, request, send_file
import assets
from vision import VisionError, prepare_images, store_images, register_vision

MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT = 200000
EXTENSIONS = {'.txt', '.md', '.pdf', '.docx'}
# PDF extractors emit control characters for figure labels and unmapped glyphs; they carry no
# text, and PostgreSQL rejects NUL. Tab and newline stay.
CONTROLS = dict.fromkeys([*range(0, 9), 11, 12, *range(14, 32), 127])
# 方正-typeset PDFs (人教版 textbooks) encode italic letters as GBK row 0xA0 at the ASCII code
# + 0x80, so pypdf reads 狓 for x and 犃 for A; none of these rare characters is in GB2312. The
# same fonts put the figure-number dash and the multiplication sign in private use, and draw
# arrow shafts with two more private-use glyphs.
FOUNDER = {ord(bytes([0xA0, code + 0x80]).decode('gbk')): chr(code)
           for code in [*range(0x41, 0x5B), *range(0x61, 0x7B)]}
FOUNDER_SYMBOLS = {0xE011: '-', 0xE012: '×', 0xE5C5: None, 0xE5C6: None}
# Display forms that PDF fonts use for ordinary characters: Kangxi radicals (⼯ for 工) and
# mathematical italic letters (𝑥 for x). Matching, retrieval and the model read the plain forms.
COMPAT = {code: unicodedata.normalize('NFKC', chr(code)) for code in [*range(0x2F00, 0x2FD6), *range(0x1D400, 0x1D800)]
          if unicodedata.normalize('NFKC', chr(code)) != chr(code)}
# Wingdings bullets come out as a letter glued to the line ("nIntroduction", "§For a…"). A
# document is treated as using them only when the pattern recurs, so prose is left alone.
BULLETS = (re.compile(r'^n(?=[A-Z0-9])', re.M), re.compile(r'^§(?=\S)', re.M))


class ImportError(ValueError):
    pass


class WebText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title = [], []
        self.ignored = 0
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.ignored += 1
        if tag == 'title':
            self.in_title = True
        if tag in {'p', 'br', 'div', 'li', 'h1', 'h2', 'h3', 'article', 'section'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.ignored = max(0, self.ignored - 1)
        if tag == 'title':
            self.in_title = False
        if tag in {'p', 'div', 'li', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        elif not self.ignored:
            self.parts.append(data)


def decode_text(raw, charset=None):
    for encoding in dict.fromkeys([charset, 'utf-8-sig', 'gb18030']):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            pass
    raise ImportError('无法识别文字编码，请将文件保存为 UTF-8 后重试。')


def clean_text(text, pdf=False):
    """Drop control characters from document text; repair PDF glyph forms (see clean_pages)."""
    return clean_pages([text])[0] if pdf else text.replace('\r\n', '\n').replace('\r', '\n').translate(CONTROLS)


def clean_pages(pages):
    """Clean each PDF page with rules decided once for the whole document.

    Deciding per document keeps page texts consistent with the stored full text, which is the
    pages joined by newlines; knowledge recognition files page ranges of the same text.
    """
    pages = [clean_text(page) for page in pages]
    whole = '\n'.join(pages)
    table = dict(COMPAT)
    if any(chr(code) in whole for code in FOUNDER):
        table |= FOUNDER | FOUNDER_SYMBOLS
    bullets = [pattern for pattern in BULLETS if len(pattern.findall(whole)) >= 5]
    result = []
    for page in pages:
        page = page.translate(table)
        for pattern in bullets:
            page = pattern.sub('• ', page)
        result.append(page)
    return result


def pdf_pages(reader):
    return clean_pages([page.extract_text() or '' for page in reader.pages])


def extract(raw, filename):
    ext = Path(filename).suffix.lower()
    if ext not in EXTENSIONS:
        raise ImportError('支持 TXT、Markdown、PDF 和 DOCX 文件。')
    if ext in {'.txt', '.md'}:
        return decode_text(raw)
    try:
        if ext == '.docx':
            from docx import Document
            from docx.table import Table
            with ZipFile(BytesIO(raw)) as archive:
                if sum(info.file_size for info in archive.infolist()) > 40 * 1024 * 1024:
                    raise ImportError('文档解压后过大，请拆分文件。')
            doc = Document(BytesIO(raw))
            lines = []
            for block in doc.iter_inner_content():
                if isinstance(block, Table):
                    lines.extend(' | '.join(cell.text for cell in row.cells) for row in block.rows)
                else:
                    lines.append(block.text)
            return clean_text('\n'.join(lines))
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(raw))
        if reader.is_encrypted:
            raise ImportError('请先解密 PDF，再上传。')
        if len(reader.pages) > 200:
            raise ImportError('PDF 最多支持 200 页，请拆分后上传。')
        return '\n'.join(pdf_pages(reader))
    except ImportError:
        raise
    except Exception as exc:
        raise ImportError('文件解析失败，请确认文件完整且格式正确。') from exc


def public_target(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ImportError('请输入公开网页的 HTTP 或 HTTPS 网址。')
        if parsed.port not in {None, 80, 443}:
            raise ImportError('仅支持标准 HTTP / HTTPS 端口。')
        host = parsed.hostname.encode('idna').decode('ascii')
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ImportError('仅支持公开互联网网址，不能导入本机或内网地址。')
        return parsed, host, port, addresses[0][4][0]
    except ImportError:
        raise
    except (ValueError, UnicodeError, socket.gaierror) as exc:
        raise ImportError('网址无效或无法解析，请检查网址。') from exc


def fetch_public(url):
    # Connect to the validated IP; retain the original host for TLS and Host.
    # Revalidate each redirect and never forward cookies or credentials.
    for _ in range(4):
        parsed, host, port, address = public_target(url)
        conn = http.client.HTTPConnection(host, port, timeout=15)
        try:
            sock = socket.create_connection((address, port), timeout=15)
            if parsed.scheme == 'https':
                try:
                    sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
                except Exception:
                    sock.close()
                    raise
            conn.sock = sock
            target = quote(parsed.path or '/', safe="/%:@!$&()*+,;=-._~")
            if parsed.query:
                target += '?' + quote(parsed.query, safe="=&%/:?@!$()*+,;~-._")
            conn.request('GET', target, headers={'User-Agent': 'Zhiye-Material-Importer/1.0', 'Accept-Encoding': 'identity'})
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader('Location')
                if not location:
                    raise ImportError('网页重定向缺少目标网址。')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ImportError(f'网页返回 HTTP {response.status}，请使用可直接访问的网址。')
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ImportError('网页或文件超过 10 MB，请使用更小的资料。')
            mime = response.headers.get_content_type()
            charset = response.headers.get_content_charset()
            return raw, mime, charset, url
        except (OSError, http.client.HTTPException) as exc:
            raise ImportError('网址读取失败，请检查网络或换一个可公开访问的网址。') from exc
        finally:
            conn.close()
    raise ImportError('网页重定向次数过多，请使用最终网址。')


def read_upload(root, upload_id, raw=False):
    """Read one stored upload so retrieval can index it; None when it is not there."""
    store = Path(root) / '.runtime' / 'uploads.sqlite3'
    if not store.exists():
        return None
    with closing(sqlite3.connect(store, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT * FROM uploads WHERE id = ?', (upload_id,)).fetchone()
    if row is None:
        return None
    # Uploads saved before course scoping have no scope columns; they read as unassigned.
    row = dict(row)
    keys = ('id', 'title', 'filename', 'source_url', 'content', 'course_id', 'class_id', 'kind') + (('raw',) if raw else ())
    return {key: row.get(key) for key in keys}


def register_uploads(app, root, check_scope=None):
    """check_scope(course_id, class_id) raises ImportError when the pair is not a real course/class."""
    register_vision(app, root)
    store = Path(root) / '.runtime' / 'uploads.sqlite3'

    def connect():
        store.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(store, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('''CREATE TABLE IF NOT EXISTS uploads (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, filename TEXT NOT NULL,
            kind TEXT NOT NULL, source_url TEXT NOT NULL, content TEXT NOT NULL,
            raw BLOB NOT NULL, created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS upload_notices (
            upload_id TEXT PRIMARY KEY, warnings TEXT NOT NULL)''')
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(uploads)')}
        for column in ('course_id', 'class_id'):
            if column not in columns:
                conn.execute(f'ALTER TABLE uploads ADD COLUMN {column} INTEGER')
        conn.commit()
        return conn

    def summary(row):
        return {key: row[key] for key in ('id', 'title', 'filename', 'kind', 'source_url', 'created_at', 'course_id', 'class_id')}

    def scope_ids(values):
        """Optional course/class pair from a form or JSON body; a class implies its course."""
        ids = []
        for key in ('course_id', 'class_id'):
            raw = values.get(key)
            if raw in (None, ''):
                ids.append(None)
            elif isinstance(raw, int) and not isinstance(raw, bool) or isinstance(raw, str) and raw.isdigit():
                ids.append(int(raw))
            else:
                raise ImportError('课程或班级编号无效，请刷新后重试。')
        if ids[1] is not None and ids[0] is None:
            raise ImportError('班级资料须同时指定课程。')
        if ids[0] is not None and check_scope:
            check_scope(*ids)
        return ids

    @app.get('/api/uploads')
    def list_uploads():
        """scope: course (course-wide only), class, course_all (course-wide and its classes), unassigned."""
        try:
            course_id, class_id = scope_ids(request.args)
        except ImportError as exc:
            return jsonify(error=str(exc)), 400
        scope = request.args.get('scope', '')
        where, params = {
            'course': ('course_id = ? AND class_id IS NULL', [course_id]),
            'class': ('class_id = ?', [class_id]),
            'course_all': ('course_id = ?', [course_id]),
            'unassigned': ('course_id IS NULL', []),
        }.get(scope, ('1 = 1', []))
        if None in params:
            return jsonify(error='请选择课程或班级。'), 400
        with closing(connect()) as conn:
            rows = conn.execute(f'SELECT id,title,filename,kind,source_url,created_at,course_id,class_id,length(content) AS characters FROM uploads WHERE {where} ORDER BY created_at DESC LIMIT 100', params).fetchall()
            notices = {r['upload_id']: json.loads(r['warnings']) for r in conn.execute('SELECT * FROM upload_notices')}
        return jsonify(uploads=[{**summary(row), 'characters': row['characters'], 'warnings': notices.get(row['id'], [])} for row in rows])

    @app.put('/api/uploads/<upload_id>/scope')
    def move_upload(upload_id):
        """File an upload under a course or one of its classes; already收录的材料 keep their own scope."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error='请求格式必须是 JSON 对象。'), 400
        try:
            course_id, class_id = scope_ids(data)
        except ImportError as exc:
            return jsonify(error=str(exc)), 400
        if course_id is None:
            return jsonify(error='请选择课程。'), 400
        with closing(connect()) as conn, conn:
            if not conn.execute('UPDATE uploads SET course_id=?,class_id=? WHERE id=?', (course_id, class_id, upload_id)).rowcount:
                return jsonify(error='资料不存在。'), 404
        return jsonify(id=upload_id, course_id=course_id, class_id=class_id)

    @app.post('/api/uploads')
    def create_upload():
        try:
            if request.mimetype == 'multipart/form-data':
                course_id, class_id = scope_ids(request.form)
                file = request.files.get('file')
                if not file or not file.filename:
                    raise ImportError('请选择要上传的资料文件。')
                filename = file.filename.replace('\\', '/').rsplit('/', 1)[-1][:180]
                raw = file.read(MAX_BYTES + 1)
                if not raw or len(raw) > MAX_BYTES:
                    raise ImportError('文件不能为空，且不能超过 10 MB。')
                content = '' if Path(filename).suffix.lower() in assets.EXTENSIONS else extract(raw, filename)
                title = request.form.get('title', '').strip() or Path(filename).stem
                kind, source_url = 'file', ''
            else:
                data = request.get_json(silent=True) or {}
                if not isinstance(data, dict) or not isinstance(data.get('url'), str):
                    raise ImportError('请输入要导入的网址。')
                course_id, class_id = scope_ids(data)
                url = data['url'].strip()
                if len(url) > 2048:
                    raise ImportError('网址过长。')
                raw, mime, charset, source_url = fetch_public(url)
                filename = unquote(urlsplit(source_url).path.rsplit('/', 1)[-1])[:180] or 'webpage.html'
                if mime in {'text/html', 'application/xhtml+xml'}:
                    parser = WebText()
                    parser.feed(decode_text(raw, charset))
                    content = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
                    title = ''.join(parser.title).strip() or urlsplit(source_url).hostname
                    filename = 'webpage.html'
                elif mime.startswith('text/'):
                    content, title = decode_text(raw, charset), filename
                elif Path(filename).suffix.lower() in {'.pdf', '.docx'}:
                    content, title = extract(raw, filename), Path(filename).stem
                else:
                    raise ImportError('网址内容不支持，请使用网页、文本、PDF 或 DOCX 的直接链接。')
                custom_title = data.get('title', '')
                if not isinstance(custom_title, str):
                    raise ImportError('资料名称格式不正确。')
                title = custom_title.strip() or title
                kind = 'url'
            warnings = []
            images = prepare_images(raw, filename, warnings=warnings)
            content = content.strip()
            if not content and not images:
                raise ImportError('未提取到有效文字。扫描版 PDF 请先进行文字识别。')
            # PDF and DOCX text is already cleaned; NUL left here means a binary or UTF-16 file.
            if '\x00' in content:
                raise ImportError('文件含有二进制内容，无法按文本读取。文本文件请另存为 UTF-8 后重试。')
            if len(content) > MAX_TEXT:
                raise ImportError('资料正文超过 20 万字符，请拆分后上传。')
            if len(title) > 120:
                raise ImportError('资料名称最多 120 字。')
            record = dict(id=uuid.uuid4().hex, title=title, filename=filename, kind=kind,
                          source_url=source_url, content=content, raw=raw,
                          created_at=datetime.now(timezone.utc).isoformat(), course_id=course_id, class_id=class_id)
            with closing(connect()) as conn, conn:
                conn.execute('''INSERT INTO uploads (id,title,filename,kind,source_url,content,raw,created_at,course_id,class_id)
                    VALUES (:id,:title,:filename,:kind,:source_url,:content,:raw,:created_at,:course_id,:class_id)''', record)
                store_images(conn, record['id'], images)
                conn.execute('INSERT INTO upload_notices VALUES (?,?)', (record['id'], json.dumps(warnings, ensure_ascii=False)))
            return jsonify(upload={**summary(record), 'characters': len(content), 'images': len(images), 'warnings': warnings}), 201
        except (ImportError, VisionError) as exc:
            return jsonify(error=str(exc)), 400

    @app.get('/api/uploads/<upload_id>')
    def upload_detail(upload_id):
        with closing(connect()) as conn:
            row = conn.execute('SELECT * FROM uploads WHERE id=?', (upload_id,)).fetchone()
            notice = conn.execute('SELECT warnings FROM upload_notices WHERE upload_id=?', (upload_id,)).fetchone()
        if row is None:
            return jsonify(error='资料不存在。'), 404
        return jsonify(upload={**summary(row), 'content': row['content'], 'characters': len(row['content']), 'warnings': json.loads(notice['warnings']) if notice else []})

    @app.get('/api/uploads/<upload_id>/download')
    def download_upload(upload_id):
        with closing(connect()) as conn:
            row = conn.execute('SELECT filename,raw FROM uploads WHERE id=?', (upload_id,)).fetchone()
        if row is None:
            return jsonify(error='资料不存在。'), 404
        if request.args.get('inline') == '1' and row['filename'].lower().endswith('.pdf'):
            # Source links open a PDF at the cited page (#page=N); only PDFs are ever shown inline.
            response = send_file(BytesIO(row['raw']), as_attachment=False, download_name=row['filename'], mimetype='application/pdf')
            response.headers['X-Content-Type-Options'] = 'nosniff'
            return response
        return send_file(BytesIO(row['raw']), as_attachment=True, download_name=row['filename'], mimetype='application/octet-stream')
