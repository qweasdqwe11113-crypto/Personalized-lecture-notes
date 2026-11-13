'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {LogSink} = require('../src/logs');

const text = sink => sink.tail().map(line => line.replace(/^\S+ \S+ /, ''));

test('output arriving in pieces is logged as whole lines', () => {
  const sink = new LogSink(null);
  sink.write('service', 'first li');
  sink.write('service', 'ne\r\nsecond\nthi');
  sink.write('pg_ctl', 'other source\n');
  sink.flush();
  assert.deepEqual(text(sink), ['[service] first line', '[service] second', '[pg_ctl] other source', '[service] thi']);
});

test('colour codes and successful requests are left out, failures are kept', () => {
  const sink = new LogSink(null);
  sink.write('service', [
    '\x1b[31m\x1b[1mWARNING: This is a development server.\x1b[0m',
    '127.0.0.1 - - [13/Nov/2026 16:33:24] "GET /static/app.js HTTP/1.1" 304 -',
    '127.0.0.1 - - [13/Nov/2026 16:33:24] "GET /api/courses HTTP/1.1" 200 -',
    '127.0.0.1 - - [13/Nov/2026 16:33:24] "\x1b[33mGET /api/missing HTTP/1.1\x1b[0m" 404 -',
    '127.0.0.1 - - [13/Nov/2026 16:33:24] "POST /api/courses HTTP/1.1" 201 -',
    '',
  ].join('\n'));
  assert.deepEqual(text(sink), [
    '[service] WARNING: This is a development server.',
    '[service] 127.0.0.1 - - [13/Nov/2026 16:33:24] "GET /api/missing HTTP/1.1" 404 -',
    '[service] 127.0.0.1 - - [13/Nov/2026 16:33:24] "POST /api/courses HTTP/1.1" 201 -',
  ]);
});

test('memory keeps the latest lines; the file rotates once it grows past the limit', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'zhiye-logs-'));
  try {
    const file = path.join(dir, 'logs', 'client.log');
    const sink = new LogSink(file, {limit: 3, maxBytes: 200});
    for (let i = 0; i < 10; i += 1) sink.line('client', `line ${i} ${'x'.repeat(30)}`);
    assert.equal(sink.tail().length, 3);
    assert.match(sink.tail().at(-1), /line 9/);
    assert.ok(fs.existsSync(`${file}.1`));
    assert.ok(fs.statSync(file).size <= 200);
    // A new session starts from what is on disk.
    const again = new LogSink(file, {maxBytes: 200});
    again.line('client', 'y'.repeat(300));
    assert.match(fs.readFileSync(`${file}.1`, 'utf8'), /y{300}/, 'rotated as soon as the limit was passed');
    assert.equal(fs.existsSync(file), false);
  } finally { fs.rmSync(dir, {recursive: true, force: true}); }
});
