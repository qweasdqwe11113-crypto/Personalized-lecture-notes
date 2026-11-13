'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const backup = require('../src/backup');

function dataFolder(root, marker, major = '17') {
  fs.mkdirSync(path.join(root, 'database'), {recursive: true});
  fs.mkdirSync(path.join(root, 'workspace', '.runtime'), {recursive: true});
  fs.writeFileSync(path.join(root, 'database', 'PG_VERSION'), `${major}\n`);
  fs.writeFileSync(path.join(root, 'database', 'postmaster.pid'), '123');
  fs.writeFileSync(path.join(root, 'workspace', '.runtime', 'uploads.sqlite3'), marker);
  fs.writeFileSync(path.join(root, 'database-secret.json'), JSON.stringify({password: marker.padEnd(16, '!')}));
  return root;
}

test('a backup copies the stopped data and can be restored into a folder with other data', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'zhiye-backup-'));
  try {
    const data = dataFolder(path.join(temp, 'Zhiye'), 'before');
    const now = new Date(2026, 10, 13, 9, 5, 7);
    const folder = backup.createBackup(data, temp, {version: '0.1.0', edition: 'release', now});
    assert.equal(path.basename(folder), '知页备份-20261113-090507');
    assert.equal(fs.existsSync(path.join(folder, 'database', 'postmaster.pid')), false, 'lock file is not copied');
    assert.throws(() => backup.createBackup(data, temp, {now}), /同名备份/);

    const manifest = backup.checkBackup(folder, data, 'release');
    assert.deepEqual([manifest.version, manifest.edition, manifest.postgres], ['0.1.0', 'release', '17']);

    fs.writeFileSync(path.join(data, 'workspace', '.runtime', 'uploads.sqlite3'), 'after');
    const aside = backup.restoreBackup(folder, data, {now: new Date(2026, 10, 14, 8, 0, 0)});
    assert.equal(fs.readFileSync(path.join(data, 'workspace', '.runtime', 'uploads.sqlite3'), 'utf8'), 'before');
    assert.equal(fs.readFileSync(path.join(aside, 'workspace', '.runtime', 'uploads.sqlite3'), 'utf8'), 'after', 'replaced data is kept');
    assert.equal(path.basename(aside), '恢复前-20261114-080000');
  } finally { fs.rmSync(temp, {recursive: true, force: true}); }
});

test('folders that are not a matching backup are refused before anything is touched', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'zhiye-backup-'));
  try {
    const data = dataFolder(path.join(temp, 'Zhiye'), 'x');
    assert.throws(() => backup.checkBackup(path.join(temp, 'random'), data, 'release'), /不是知页备份/);

    const test = backup.createBackup(dataFolder(path.join(temp, 'Zhiye-Test'), 'y'), temp, {version: '0.1.0', edition: 'demo', now: new Date(2026, 0, 1)});
    assert.throws(() => backup.checkBackup(test, data, 'release'), /测试版/);

    const old = backup.createBackup(dataFolder(path.join(temp, 'Old'), 'z', '16'), temp, {version: '0.0.9', edition: 'release', now: new Date(2026, 0, 2)});
    assert.throws(() => backup.checkBackup(old, data, 'release'), /PostgreSQL 16/);

    fs.rmSync(path.join(old, 'database'), {recursive: true});
    assert.throws(() => backup.checkBackup(old, data, 'release'), /缺少数据库文件/);
  } finally { fs.rmSync(temp, {recursive: true, force: true}); }
});
