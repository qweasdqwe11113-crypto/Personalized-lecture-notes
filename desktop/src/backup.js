'use strict';
/* Cold backups: with the database stopped, the database folder and the workspace (uploaded
   material, grading records, model settings) are copied as they are. Restoring moves the current
   data aside instead of deleting it. */
const fs = require('node:fs');
const path = require('node:path');

const MANIFEST = 'zhiye-backup.json';
const PARTS = ['database', 'workspace', 'database-secret.json'];

function stamp(date = new Date()) {
  const p = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}${p(date.getMonth() + 1)}${p(date.getDate())}-${p(date.getHours())}${p(date.getMinutes())}${p(date.getSeconds())}`;
}

/**
 * Recursive copy with the basic file calls. fs.cpSync is not used: on Windows, Node 22 writes to
 * the wrong place when a path contains characters outside ASCII, such as 知页备份 or a Chinese
 * user name.
 */
function copyTree(source, target) {
  const stat = fs.lstatSync(source);
  if (stat.isDirectory()) {
    fs.mkdirSync(target, {recursive: true});
    for (const name of fs.readdirSync(source)) copyTree(path.join(source, name), path.join(target, name));
    // Keep the folder's permissions: PostgreSQL refuses a data folder that others may read. Set
    // last, so that a read-only folder can still be filled. (Files keep theirs in copyFileSync.)
    if (process.platform !== 'win32') fs.chmodSync(target, stat.mode & 0o777);
  } else if (stat.isSymbolicLink()) {
    fs.symlinkSync(fs.readlinkSync(source), target);
  } else {
    fs.copyFileSync(source, target);
  }
}

/** Recursive delete with the basic file calls, for the same reason as copyTree. */
function removeTree(target) {
  let stat;
  try { stat = fs.lstatSync(target); } catch (error) { if (error.code === 'ENOENT') return; throw error; }
  if (stat.isDirectory()) {
    for (const name of fs.readdirSync(target)) removeTree(path.join(target, name));
    fs.rmdirSync(target);
    return;
  }
  try {
    fs.unlinkSync(target);
  } catch (error) {
    // Windows refuses to delete read-only files.
    if (error.code !== 'EPERM' || stat.isSymbolicLink()) throw error;
    fs.chmodSync(target, 0o666);
    fs.unlinkSync(target);
  }
}

function postgresMajor(dataDir) {
  try { return fs.readFileSync(path.join(dataDir, 'database', 'PG_VERSION'), 'utf8').trim(); } catch { return null; }
}

/** Copy the stopped data folder into parent/<name>, by default 知页备份-<time>; returns the new folder. */
function createBackup(dataDir, parent, {version, edition, now = new Date(), name = `知页备份-${stamp(now)}`} = {}) {
  const target = path.join(parent, name);
  if (fs.existsSync(target)) throw new Error('同名备份已存在，请稍后再试。');
  fs.mkdirSync(target, {recursive: true});
  for (const part of PARTS) {
    const source = path.join(dataDir, part);
    if (fs.existsSync(source)) copyTree(source, path.join(target, part));
  }
  // Runtime lock files belong to the server that wrote them, not to the copy.
  fs.rmSync(path.join(target, 'database', 'postmaster.pid'), {force: true});
  fs.writeFileSync(path.join(target, MANIFEST), JSON.stringify({
    app: '知页', version, edition, postgres: postgresMajor(dataDir), created_at: now.toISOString(),
  }, null, 2));
  return target;
}

function readManifest(folder) {
  try {
    const value = JSON.parse(fs.readFileSync(path.join(folder, MANIFEST), 'utf8'));
    return value && value.app === '知页' ? value : null;
  } catch {
    return null;
  }
}

/** Check that folder is a usable backup for this data folder; returns its manifest. */
function checkBackup(folder, dataDir, edition) {
  const manifest = readManifest(folder);
  if (!manifest) throw new Error('所选文件夹不是知页备份（缺少 zhiye-backup.json）。');
  if (!fs.existsSync(path.join(folder, 'database', 'PG_VERSION'))) throw new Error('备份中缺少数据库文件。');
  const current = postgresMajor(dataDir);
  if (current && manifest.postgres && manifest.postgres !== current) {
    throw new Error(`备份来自 PostgreSQL ${manifest.postgres}，当前为 ${current}，无法直接恢复。`);
  }
  if (manifest.edition && edition && manifest.edition !== edition) {
    throw new Error(`这份备份来自${manifest.edition === 'release' ? '正式版' : '测试版'}，请在对应版本中恢复。`);
  }
  return manifest;
}

/** Replace the stopped data with the backup; the previous data goes to <dataDir>/恢复前-<time>. */
function restoreBackup(folder, dataDir, {now = new Date()} = {}) {
  const aside = path.join(dataDir, `恢复前-${stamp(now)}`);
  fs.mkdirSync(aside, {recursive: true});
  for (const part of PARTS) {
    const current = path.join(dataDir, part);
    if (fs.existsSync(current)) fs.renameSync(current, path.join(aside, part));
  }
  for (const part of PARTS) {
    const source = path.join(folder, part);
    if (fs.existsSync(source)) copyTree(source, path.join(dataDir, part));
  }
  // Backups made before copyTree kept folder permissions hold a database folder others may read.
  const database = path.join(dataDir, 'database');
  if (process.platform !== 'win32' && fs.existsSync(database)) fs.chmodSync(database, 0o700);
  return aside;
}

module.exports = {createBackup, checkBackup, restoreBackup, readManifest, stamp, copyTree, removeTree, MANIFEST};
