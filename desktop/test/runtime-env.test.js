'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {cleanEnv, shortPath, plainPath} = require('../src/processes');
const {serviceEnv} = require('../src/backend');
const {initdbArgs, readSecret, writeSecret, CONF} = require('../src/postgres');
const {isFree, pickPort} = require('../src/ports');

test('settings from the user shell do not reach the service or PostgreSQL', () => {
  const base = {PATH: '/usr/bin', HOME: '/home/a', LANG: 'zh_CN.UTF-8', DATABASE_URL: 'postgresql://elsewhere', PORT: '80',
    QWEN_API_KEY: 'k', VLM_MODEL: 'm', ZHIYE_EDITION: 'demo', PGPASSWORD: 'p', PGDATA: '/x', PYTHONPATH: '/x',
    LD_LIBRARY_PATH: '/x', FLASK_DEBUG: '1', MODEL_CONCURRENCY: '9', PPT_AESTHETIC_MODEL: 'm'};
  assert.deepEqual(cleanEnv({EXTRA: '1'}, base), {PATH: '/usr/bin', HOME: '/home/a', LANG: 'zh_CN.UTF-8', EXTRA: '1'});
});

test('the service gets its database, port, data folder and edition from the client', () => {
  const env = serviceEnv({workspace: '/data/workspace', edition: 'release', databaseUrl: 'postgresql://x', port: 38660, packaged: true});
  assert.equal(env.DATABASE_URL, 'postgresql://x');
  assert.equal(env.PORT, '38660');
  assert.equal(env.ZHIYE_DATA_DIR, '/data/workspace');
  assert.equal(env.ZHIYE_EDITION, 'release');
  assert.equal(env.PYTHONUTF8, '1');
  assert.equal(env.PYTHONDONTWRITEBYTECODE, '1');
  assert.equal(serviceEnv({workspace: 'w', edition: 'demo', databaseUrl: 'd', port: 1, packaged: false}).ZHIYE_EDITION, 'demo');
  assert.equal(serviceEnv({workspace: 'w', edition: 'demo', databaseUrl: 'd', port: 1, packaged: false}).PYTHONDONTWRITEBYTECODE, undefined);
});

test('the database cluster is private: password login, local address only, UTF-8', () => {
  const args = initdbArgs('/data/database', '/tmp/pw');
  assert.ok(args.includes('--auth=scram-sha-256'));
  assert.ok(args.includes('--encoding=UTF8'));
  assert.ok(args.includes('--pwfile=/tmp/pw'));
  assert.match(CONF, /^listen_addresses = '127\.0\.0\.1'$/m);
  assert.match(CONF, /^unix_socket_directories = ''$/m);
});

test('the database password is stored privately and short or broken secrets are refused', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'zhiye-secret-'));
  try {
    const file = path.join(dir, 'database-secret.json');
    writeSecret(file, 'a'.repeat(32));
    assert.equal(readSecret(file), 'a'.repeat(32));
    if (process.platform !== 'win32') assert.equal(fs.statSync(file).mode & 0o777, 0o600);
    fs.writeFileSync(file, JSON.stringify({password: 'short'}));
    assert.equal(readSecret(file), null);
    fs.writeFileSync(file, '{');
    assert.equal(readSecret(file), null);
  } finally { fs.rmSync(dir, {recursive: true, force: true}); }
});

test('a busy preferred port gives way to a free one', async () => {
  const server = net.createServer();
  await new Promise(resolve => server.listen({port: 0, host: '127.0.0.1'}, resolve));
  const busy = server.address().port;
  try {
    assert.equal(await isFree(busy), false);
    const picked = await pickPort(busy);
    assert.equal(picked.preferred, false);
    assert.notEqual(picked.port, busy);
  } finally { server.close(); }
  const free = await pickPort(busy);
  assert.deepEqual(free, {port: busy, preferred: true});
});

test('only plain ASCII paths are handed to PostgreSQL as they are', () => {
  assert.equal(plainPath('C:\\Users\\RUNNER~1\\AppData\\Local\\Temp\\9A3F~1\\database'), true);
  assert.equal(plainPath('D:\\Program Files\\知页'), false);
  assert.equal(plainPath('C:\\Users\\?? ??\\database'), false, 'characters already lost to "?"');
  assert.equal(plainPath(''), false);
  if (process.platform !== 'win32') assert.equal(shortPath('/home/林晓/zhiye'), '/home/林晓/zhiye');
});
