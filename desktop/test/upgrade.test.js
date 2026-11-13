'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const backup = require('../src/backup');
const upgrade = require('../src/upgrade');
const {Runtime} = require('../src/runtime');

function temp(t, prefix = 'zhiye-upgrade-') {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  return dir;
}

/** A stopped data folder as the client leaves it, last run by `version` (null: nothing recorded). */
function dataFolder(root, version, marker = 'old data') {
  fs.mkdirSync(path.join(root, 'database'), {recursive: true});
  fs.mkdirSync(path.join(root, 'workspace', '.runtime'), {recursive: true});
  fs.mkdirSync(path.join(root, 'logs'), {recursive: true});
  fs.writeFileSync(path.join(root, 'database', 'PG_VERSION'), '17\n');
  fs.writeFileSync(path.join(root, 'database', 'postmaster.pid'), '123');
  fs.writeFileSync(path.join(root, 'workspace', '.runtime', 'uploads.sqlite3'), marker);
  fs.writeFileSync(path.join(root, 'database-secret.json'), JSON.stringify({password: 'x'.repeat(32)}));
  if (version) upgrade.writeRecord(root, {version, edition: 'release'});
  return root;
}

const at = (day, hour = 9) => new Date(2026, 10, day, hour, 0, 0);
const names = dir => fs.readdirSync(dir).filter(n => n.startsWith(upgrade.PREFIX)).sort();

function calls() {
  const seen = [];
  return {seen, ensureStopped: async () => { seen.push('stopped'); }, onBackup: info => seen.push(['backup', info.previous, info.version])};
}

test('versions compare by package.json version, pre-releases before their release', () => {
  assert.equal(upgrade.compareVersions('0.1.0', '0.2.0'), -1);
  assert.equal(upgrade.compareVersions('0.10.0', '0.9.9'), 1);
  assert.equal(upgrade.compareVersions('1.2.3', '1.2.3'), 0);
  assert.equal(upgrade.compareVersions('1.0.0-beta.2', '1.0.0'), -1);
  assert.equal(upgrade.compareVersions('1.0.0-beta.10', '1.0.0-beta.2'), 1);
  assert.equal(upgrade.compareVersions('1.0.0-beta', '1.0.0-beta.1'), -1);
  assert.equal(upgrade.compareVersions('1.0.0-1', '1.0.0-alpha'), -1);
  assert.equal(upgrade.compareVersions('1.0.0+build.5', '1.0.0'), 0);
  for (const bad of ['', 'smoke', '1.0', 'v1.0.0', '../../x', null, 3]) assert.equal(upgrade.compareVersions(bad, '1.0.0'), null, String(bad));
});

test('first start in a new data folder records the version and makes no backup', async t => {
  const data = path.join(temp(t), 'zhiye');
  fs.mkdirSync(data);
  const spy = calls();
  const result = await upgrade.prepare({dataDir: data, version: '0.2.0', edition: 'release', ...spy, now: at(13)});
  assert.equal(result.kind, 'fresh');
  assert.deepEqual(spy.seen, [], 'nothing stopped, nothing copied');
  assert.deepEqual(names(data), []);
  assert.equal(upgrade.readRecord(data).version, '0.2.0');
  // A first start that fails after creating the database is not mistaken for an upgrade.
  dataFolder(data, null);
  assert.equal(upgrade.decide(data, '0.2.0').kind, 'same');
});

test('the same version starts without a backup', async t => {
  const data = dataFolder(path.join(temp(t), 'zhiye'), '0.2.0');
  const spy = calls();
  const result = await upgrade.prepare({dataDir: data, version: '0.2.0', edition: 'release', ...spy, now: at(13)});
  assert.deepEqual(result, {kind: 'same', previous: '0.2.0'});
  assert.deepEqual(spy.seen, []);
  assert.deepEqual(names(data), []);
});

test('an upgrade copies the stopped data first and keeps the newest three copies', async t => {
  const data = dataFolder(path.join(temp(t), 'zhiye'), '0.1.0');
  // Look-alikes that pruning must leave alone: no manifest, a manual backup, a link to a backup.
  fs.mkdirSync(path.join(data, '升级前备份-手工-20200101-000000'));
  const manual = backup.createBackup(data, data, {version: '0.1.0', edition: 'release', now: at(1)});
  const outside = backup.createBackup(data, temp(t), {version: '0.0.1', edition: 'release', now: at(2),
    name: '升级前备份-0.0.1-20200102-000000'});
  if (process.platform !== 'win32') fs.symlinkSync(outside, path.join(data, '升级前备份-0.0.2-20200103-000000'));

  const spy = calls();
  spy.ensureStopped = async () => {
    spy.seen.push('stopped');
    assert.deepEqual(upgrade.listUpgradeBackups(data), [], 'the database is stopped before anything is copied');
  };
  const first = await upgrade.prepare({dataDir: data, version: '0.2.0', edition: 'release', ...spy, now: at(13, 9)});
  assert.equal(first.kind, 'upgrade');
  assert.equal(path.basename(first.backup), '升级前备份-0.1.0-20261113-090000');
  assert.deepEqual(spy.seen, [['backup', '0.1.0', '0.2.0'], 'stopped']);

  // The copy is an ordinary backup of the old data that 「从备份恢复」 accepts.
  const manifest = backup.checkBackup(first.backup, data, 'release');
  assert.deepEqual([manifest.version, manifest.edition, manifest.postgres], ['0.1.0', 'release', '17']);
  assert.equal(fs.readFileSync(path.join(first.backup, 'workspace', '.runtime', 'uploads.sqlite3'), 'utf8'), 'old data');
  assert.equal(fs.existsSync(path.join(first.backup, 'database', 'postmaster.pid')), false);
  assert.equal(fs.existsSync(path.join(first.backup, 'logs')), false, 'only the backup parts are copied');
  assert.deepEqual(upgrade.readRecord(data).pending, {from: '0.1.0', to: '0.2.0', backup: '升级前备份-0.1.0-20261113-090000',
    created_at: at(13, 9).toISOString()});

  upgrade.recordStart(data, {version: '0.2.0', edition: 'release'});
  for (const [version, day] of [['0.3.0', 14], ['0.4.0', 15], ['0.5.0', 16]]) {
    const result = await upgrade.prepare({dataDir: data, version, edition: 'release', now: at(day)});
    assert.equal(result.kind, 'upgrade');
    upgrade.recordStart(data, {version, edition: 'release'});
  }
  assert.deepEqual(upgrade.listUpgradeBackups(data).map(b => b.version), ['0.4.0', '0.3.0', '0.2.0'], 'the oldest one is gone');
  assert.ok(fs.existsSync(path.join(data, '升级前备份-手工-20200101-000000')), 'a folder without a manifest stays');
  assert.ok(fs.existsSync(manual), 'manual backups are not counted or deleted');
  assert.ok(fs.existsSync(path.join(outside, 'database', 'PG_VERSION')), 'a linked folder is neither followed nor deleted');
  assert.equal(fs.readFileSync(path.join(data, 'workspace', '.runtime', 'uploads.sqlite3'), 'utf8'), 'old data', 'the data itself is untouched');
});

test('the backup just made is kept even when the clock was turned back', async t => {
  const data = dataFolder(path.join(temp(t), 'zhiye'), '0.1.0');
  for (const [version, day] of [['0.2.0', 20], ['0.3.0', 21], ['0.4.0', 22]]) {
    await upgrade.prepare({dataDir: data, version, edition: 'release', now: at(day)});
    upgrade.recordStart(data, {version, edition: 'release'});
  }
  const result = await upgrade.prepare({dataDir: data, version: '0.5.0', edition: 'release', now: at(1)});
  assert.ok(fs.existsSync(result.backup));
  assert.deepEqual(upgrade.listUpgradeBackups(data).map(b => b.version).sort(), ['0.2.0', '0.3.0', '0.4.0']);
  assert.equal(path.basename(result.backup), '升级前备份-0.4.0-20261101-090000');
});

test('a failed backup stops the upgrade and leaves the data and the record as they were', async t => {
  const data = dataFolder(path.join(temp(t), 'zhiye'), '0.1.0');
  const kept = (await upgrade.prepare({dataDir: data, version: '0.1.5', edition: 'release', now: at(10)})).backup;
  upgrade.recordStart(data, {version: '0.1.5', edition: 'release'});
  for (const day of [11, 12]) {
    const name = `升级前备份-0.1.${day - 10}-202611${day}-090000`;
    backup.createBackup(data, data, {version: `0.1.${day - 10}`, edition: 'release', now: at(day), name});
  }
  const record = fs.readFileSync(path.join(data, upgrade.RECORD), 'utf8');
  t.mock.method(backup, 'createBackup', (dataDir, parent, {name}) => {
    fs.mkdirSync(path.join(parent, name, 'database'), {recursive: true});
    fs.writeFileSync(path.join(parent, name, 'database', 'half'), 'x');
    throw Object.assign(new Error('ENOSPC: no space left on device, copyfile'), {code: 'ENOSPC'});
  });
  const lines = [];
  await assert.rejects(
    upgrade.prepare({dataDir: data, version: '0.2.0', edition: 'release', log: line => lines.push(line), now: at(13)}),
    error => /升级前备份没有完成：磁盘空间不足（ENOSPC）/.test(error.message) && error.title === '升级前备份没有完成' && Boolean(error.hint));
  assert.equal(fs.existsSync(path.join(data, '升级前备份-0.1.5-20261113-090000')), false, 'the half copy is removed');
  assert.equal(fs.readFileSync(path.join(data, upgrade.RECORD), 'utf8'), record, 'still the old version, nothing pending');
  assert.ok(fs.existsSync(kept), 'no older backup is pruned after a failure');
  assert.equal(upgrade.listUpgradeBackups(data).length, 3);
  assert.ok(lines.some(line => line.startsWith('升级前备份失败')));

  // The database stays stopped when the copy cannot even begin.
  t.mock.restoreAll();
  await assert.rejects(upgrade.prepare({dataDir: data, version: '0.2.0', edition: 'release', now: at(13),
    ensureStopped: async () => { throw new Error('数据库仍在运行'); }}), /升级前备份没有完成：数据库仍在运行/);
  assert.equal(upgrade.decide(data, '0.2.0').kind, 'upgrade');
});

test('a retry after a failed upgrade reuses the backup instead of copying half-upgraded data', async t => {
  const data = dataFolder(path.join(temp(t), 'zhiye'), '0.1.0');
  const first = await upgrade.prepare({dataDir: data, version: '0.2.0', edition: 'release', now: at(13, 9)});
  // init_db.py failed: the start did not finish, so nothing was recorded.
  const spy = calls();
  const again = await upgrade.prepare({dataDir: data, version: '0.2.0', edition: 'release', ...spy, now: at(13, 10)});
  assert.deepEqual(again, {kind: 'upgrade', previous: '0.1.0', backup: first.backup, reused: true});
  assert.deepEqual(spy.seen, []);
  assert.equal(names(data).length, 1);

  // A pending entry that points elsewhere is not trusted.
  upgrade.writeRecord(data, {...upgrade.readRecord(data), pending: {from: '0.1.0', to: '0.2.0', backup: '../zhiye'}});
  const third = await upgrade.prepare({dataDir: data, version: '0.2.0', edition: 'release', now: at(13, 11)});
  assert.equal(third.reused, false);
});

test('data without a recorded version is backed up as 未知版本', async t => {
  const data = dataFolder(path.join(temp(t), 'zhiye'), null);
  const result = await upgrade.prepare({dataDir: data, version: '0.2.0', edition: 'release', now: at(13)});
  assert.equal(path.basename(result.backup), '升级前备份-未知版本-20261113-090000');
  assert.equal(backup.readManifest(result.backup).version, null);
  // A damaged or tampered record counts as unknown; its text never becomes part of a path.
  upgrade.recordStart(data, {version: '0.2.0', edition: 'release'});
  fs.writeFileSync(path.join(data, upgrade.RECORD), JSON.stringify({version: '../../etc'}));
  assert.deepEqual(upgrade.decide(data, '0.2.0').previous, null);
  fs.writeFileSync(path.join(data, upgrade.RECORD), '{');
  assert.equal(upgrade.decide(data, '0.2.0').kind, 'upgrade');
});

test('data last run by a newer client is refused without touching it', async t => {
  const data = dataFolder(path.join(temp(t), 'zhiye'), '0.3.0');
  const spy = calls();
  await assert.rejects(upgrade.prepare({dataDir: data, version: '0.2.0', edition: 'release', ...spy, now: at(13)}),
    error => error.code === 'ZHIYE_DOWNGRADE' && /上次由知页 0\.3\.0 打开，比当前的 0\.2\.0 新/.test(error.message) &&
      /从备份恢复/.test(error.hint));
  assert.deepEqual(spy.seen, []);
  assert.deepEqual(names(data), []);
  assert.equal(upgrade.readRecord(data).version, '0.3.0');
});

test('restoring: backups from newer clients are refused, and the restored data starts without another copy', async t => {
  const dir = temp(t);
  const data = dataFolder(path.join(dir, 'zhiye'), '0.3.0', 'new data');
  assert.throws(() => upgrade.checkRestore({version: '0.4.0'}, '0.3.0'), /来自知页 0\.4\.0，比当前的 0\.3\.0 新/);
  for (const ok of ['0.3.0', '0.1.0', null, 'smoke']) upgrade.checkRestore({version: ok}, '0.3.0');

  // Back on 0.2.0 after trying 0.3.0: restore the copy made before that upgrade.
  const old = backup.createBackup(dataFolder(path.join(dir, 'old'), null), data, {version: '0.2.0', edition: 'release', now: at(1),
    name: '升级前备份-0.2.0-20261101-090000'});
  upgrade.checkRestore(backup.checkBackup(old, data, 'release'), '0.2.0');
  backup.restoreBackup(old, data, {now: at(13)});
  upgrade.recordRestore(data, {version: '0.2.0', edition: 'release', from: old});
  assert.equal(upgrade.readRecord(data).restored_from, '升级前备份-0.2.0-20261101-090000');
  const result = await upgrade.prepare({dataDir: data, version: '0.2.0', edition: 'release', now: at(13)});
  assert.equal(result.kind, 'same');
  assert.equal(fs.readFileSync(path.join(data, 'workspace', '.runtime', 'uploads.sqlite3'), 'utf8'), 'old data');
});

test('paths with Chinese characters: data folder, backup names and file names', async t => {
  const data = dataFolder(path.join(temp(t, '知页 升级-'), '林晓 的数据', 'zhiye'), '0.1.0');
  fs.mkdirSync(path.join(data, 'workspace', '资料', '第一章'), {recursive: true});
  fs.writeFileSync(path.join(data, 'workspace', '资料', '第一章', '数列极限.txt'), '内容');
  let latest;
  for (const [version, day] of [['0.2.0', 13], ['0.3.0', 14], ['0.4.0', 15], ['0.5.0', 16]]) {
    latest = await upgrade.prepare({dataDir: data, version, edition: 'release', now: at(day)});
    upgrade.recordStart(data, {version, edition: 'release'});
  }
  assert.equal(fs.readFileSync(path.join(latest.backup, 'workspace', '资料', '第一章', '数列极限.txt'), 'utf8'), '内容');
  assert.deepEqual(names(data), ['升级前备份-0.2.0-20261114-090000', '升级前备份-0.3.0-20261115-090000', '升级前备份-0.4.0-20261116-090000']);
  backup.checkBackup(latest.backup, data, 'release');
});

// ---------------------------------------------------------------- inside the start-up sequence

function fakeServices(runtime, order) {
  runtime.postgres = {
    port: null,
    running: async () => false,
    ensureCluster: async () => { order.push('ensureCluster'); return false; },
    start: async () => { order.push('postgres'); runtime.postgres.port = 5999; return 5999; },
    stop: async () => { runtime.postgres.port = null; },
    url: () => 'postgresql://zhiye@127.0.0.1:5999/x',
  };
  runtime.backend = {
    url: null,
    migrate: async () => { order.push('migrate'); },
    start: async (_db, port) => { order.push('service'); runtime.backend.url = `http://127.0.0.1:${port}`; return runtime.backend.url; },
    stop: async () => {},
  };
}

function makeRuntime(data, version) {
  return new Runtime({resources: {python: 'python', backend: 'backend', postgres: 'postgres'}, dataDir: data, edition: 'release',
    packaged: true, version});
}

test('the client backs up before the database starts, shows it, and records the version once running', async t => {
  const data = dataFolder(path.join(temp(t), 'zhiye'), '0.1.0');
  const runtime = makeRuntime(data, '0.2.0');
  const order = [];
  const progress = [];
  fakeServices(runtime, order);
  runtime.on('progress', text => progress.push(text));
  runtime.on('step', step => { if (step.id === 'prepare' && step.state === 'running' && step.detail) order.push(step.detail); });
  await runtime.start();
  assert.deepEqual(order, ['正在备份升级前的数据…', 'ensureCluster', 'postgres', 'migrate', 'service']);
  assert.deepEqual(progress, ['正在备份升级前的数据…', null]);
  assert.equal(upgrade.listUpgradeBackups(data).length, 1);
  assert.deepEqual(Object.keys(upgrade.readRecord(data)).sort(), ['edition', 'updated_at', 'version']);
  assert.equal(upgrade.readRecord(data).version, '0.2.0');
  await runtime.stop();
  const log = fs.readFileSync(path.join(data, 'logs', 'client.log'), 'utf8');
  assert.match(log, /数据上次由知页 0\.1\.0 打开，当前为 0\.2\.0/);
  assert.match(log, /升级前备份完成/);
  assert.match(log, /已从 0\.1\.0 升级到 0\.2\.0/);

  const again = makeRuntime(data, '0.2.0');
  fakeServices(again, []);
  await again.start();
  assert.equal(upgrade.listUpgradeBackups(data).length, 1, 'no second backup for the same version');
  await again.stop();
});

test('the client does not start the database when the backup fails', async t => {
  const data = dataFolder(path.join(temp(t), 'zhiye'), '0.1.0');
  const runtime = makeRuntime(data, '0.2.0');
  const order = [];
  fakeServices(runtime, order);
  t.mock.method(backup, 'createBackup', () => { throw Object.assign(new Error('EBUSY: resource busy or locked'), {code: 'EBUSY'}); });
  await assert.rejects(runtime.start(), error => error.step === 'prepare' && /升级前备份没有完成：文件被其他程序占用/.test(error.message));
  assert.deepEqual(order, [], 'neither PostgreSQL nor init_db.py ran');
  assert.equal(runtime.state, 'stopped');
  assert.equal(upgrade.readRecord(data).version, '0.1.0');
  assert.match(fs.readFileSync(path.join(data, 'logs', 'client.log'), 'utf8'), /检查数据目录失败：升级前备份没有完成/);
});

test('the client refuses data from a newer version before the database starts', async t => {
  const data = dataFolder(path.join(temp(t), 'zhiye'), '0.3.0');
  const runtime = makeRuntime(data, '0.2.0');
  const order = [];
  fakeServices(runtime, order);
  await assert.rejects(runtime.start(), error => error.code === 'ZHIYE_DOWNGRADE' && error.title === '数据来自更新的版本');
  assert.deepEqual(order, []);
});

test('a database left running by a crashed client is stopped before it is copied', async t => {
  const data = dataFolder(path.join(temp(t), 'zhiye'), '0.1.0');
  const runtime = makeRuntime(data, '0.2.0');
  fakeServices(runtime, []);
  let up = true;
  runtime.postgres.running = async () => up;
  runtime.postgres.stop = async () => { up = false; };
  await runtime.start();
  assert.equal(upgrade.listUpgradeBackups(data).length, 1);
  await runtime.stop();

  const stuck = makeRuntime(data, '0.3.0');
  const order = [];
  fakeServices(stuck, order);
  stuck.postgres.running = async () => true;
  await assert.rejects(stuck.start(), /上次没有正常关闭的数据库仍在运行/);
  assert.deepEqual(order, []);
  assert.equal(upgrade.listUpgradeBackups(data).length, 1);
});
