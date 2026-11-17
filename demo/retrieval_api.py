"""Routes of the retrieval index: its state per knowledge point, building it, and adopting an upload as material."""
import psycopg
from flask import jsonify, request

from embed import EmbeddingError
from retrieval import adopt_content, index_knowledge, status as retrieval_status
from uploads import read_upload


def register_retrieval(app, db, topics, embedder, folder):
    """topics() is the lesson catalog, embedder() the configured embedding client, folder() the data folder;
    all three are read per request, so a test or a settings change replaces them."""
    @app.get('/api/retrieval/status')
    def retrieval_state():
        """Index state per knowledge point for the embedding model currently configured."""
        topic_key = request.args.get('topic', '')
        if topic_key not in topics():
            return jsonify(error='请选择有效主题。'), 400
        try:
            with db() as conn:
                return jsonify(retrieval_status(conn, embedder(), topics()[topic_key]['knowledge_ids']))
        except psycopg.Error:
            return jsonify(error='数据库暂不可用，请检查本机 PostgreSQL。'), 503


    @app.post('/api/retrieval/index')
    def build_retrieval_index():
        """Chunk and embed every material of one topic. Re-indexing replaces its own rows only."""
        data = request.get_json(silent=True)
        if (not isinstance(data, dict) or not isinstance(data.get('topic'), str)
                or data['topic'] not in topics()):
            return jsonify(error='请选择有效主题。'), 400
        ids = topics()[data['topic']]['knowledge_ids']
        try:
            with db() as conn:
                indexed = index_knowledge(conn, embedder(), ids, force=bool(data.get('force')))
                return jsonify(indexed_materials=indexed, **retrieval_status(conn, embedder(), ids))
        except EmbeddingError as exc:
            return jsonify(error=str(exc)), 502
        except psycopg.Error:
            return jsonify(error='数据库暂不可用，索引未写入。'), 503


    @app.post('/api/retrieval/adopt')
    def adopt_upload():
        """Adopt a stored upload as a course material of one knowledge point, then index it."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error='请求格式必须是 JSON 对象。'), 400
        upload_id, knowledge_id = data.get('upload_id'), data.get('knowledge_id')
        if not isinstance(upload_id, str) or type(knowledge_id) is not int:
            return jsonify(error='请选择要收录的资料和知识点。'), 400
        try:
            with db() as conn:
                if not conn.execute('SELECT 1 FROM knowledge_points WHERE id=%s', (knowledge_id,)).fetchone():
                    return jsonify(error='知识点不存在。'), 400
        except psycopg.Error:
            return jsonify(error='数据库暂不可用，请检查本机 PostgreSQL。'), 503
        record = read_upload(folder(), upload_id)
        if record is None:
            return jsonify(error='资料不存在。'), 404
        try:
            with db() as conn:
                return jsonify(adopt_content(conn, embedder(), knowledge_id=knowledge_id, title=record['title'],
                                             content=record['content'], source_title=record['filename'],
                                             source_url=f'upload:{upload_id}', section='')), 201
        except EmbeddingError as exc:
            return jsonify(error=str(exc)), 502
        except psycopg.Error:
            return jsonify(error='数据库暂不可用，资料未收录。'), 503
