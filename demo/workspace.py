"""Settings of this workspace kept in the database: whether the tour has been seen, and the course and class
last chosen at the top of the pages."""
import re

import psycopg
from flask import jsonify, request
from psycopg.types.json import Jsonb

TOUR_SETTING = 'workspace_tour'
TOUR_OUTCOMES = ('finished', 'skipped')
SCOPE_SETTING = 'workspace_scope'


def read_setting(conn, key):
    row = conn.execute('SELECT value FROM workspace_settings WHERE key=%s', (key,)).fetchone()
    return row['value'] if row else None


def write_setting(conn, key, value):
    conn.execute('INSERT INTO workspace_settings(key,value) VALUES (%s,%s) '
                 'ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()', (key, Jsonb(value)))


def clean_scope(body):
    """The selectors' memory: the current course, and the class last chosen in each course
    ({"<course id>": class id or null}). None when it is not that shape."""
    if not isinstance(body, dict) or not isinstance(body.get('classes', {}), dict):
        return None
    course, classes = body.get('course'), body.get('classes', {})
    positive = lambda value: type(value) is int and 0 < value < 2**31
    if (course is not None and not positive(course)) or len(classes) > 500:
        return None
    for key, value in classes.items():
        if not re.fullmatch(r'[1-9][0-9]{0,8}', key) or (value is not None and not positive(value)):
            return None
    return {'course': course, 'classes': dict(classes)}


def register_workspace(app, db, released):
    """released() says whether this is the desktop release, where the tour opens by itself once."""
    @app.get('/api/onboarding')
    def onboarding():
        """Whether the workspace tour opens by itself: once per data folder, in the desktop release."""
        try:
            with db() as conn:
                seen = read_setting(conn, TOUR_SETTING) is not None
        except psycopg.Error:
            return jsonify(error='数据库暂不可用。'), 503
        return jsonify(tour={'seen': seen, 'auto': released() and not seen})


    @app.post('/api/onboarding/tour')
    def record_tour():
        """The teacher finished or skipped the tour; it no longer opens by itself."""
        body = request.get_json(silent=True) or {}
        outcome, step = body.get('outcome'), body.get('step')
        if outcome not in TOUR_OUTCOMES or type(step) is not int or not 1 <= step <= 50:
            return jsonify(error='引导状态无效。'), 400
        try:
            with db() as conn:
                write_setting(conn, TOUR_SETTING, {'outcome': outcome, 'step': step})
        except psycopg.Error:
            return jsonify(error='数据库暂不可用，引导状态未保存。'), 503
        return jsonify(tour={'seen': True, 'auto': False})


    @app.get('/api/workspace-scope')
    def workspace_scope():
        """The course and class last chosen at the top of the pages. Kept here rather than in the
        browser, which keeps its storage per port: the desktop client moves to another port when its
        own is taken, and the choice would be lost."""
        try:
            with db() as conn:
                scope = read_setting(conn, SCOPE_SETTING)
        except psycopg.Error:
            return jsonify(error='数据库暂不可用。'), 503
        return jsonify(scope=scope)


    @app.put('/api/workspace-scope')
    def remember_workspace_scope():
        scope = clean_scope(request.get_json(silent=True))
        if scope is None:
            return jsonify(error='课程与班级选择无效。'), 400
        try:
            with db() as conn:
                write_setting(conn, SCOPE_SETTING, scope)
        except psycopg.Error:
            return jsonify(error='数据库暂不可用，课程与班级选择未保存。'), 503
        return jsonify(scope=scope)
