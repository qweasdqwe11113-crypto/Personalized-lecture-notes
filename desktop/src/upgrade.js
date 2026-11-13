'use strict';
/* Backups before an upgrade. The data folder remembers which client version last ran it
   (last-version.json). When a newer client finds a database there, it copies the stopped data into
   <data folder>/升级前备份-<previous version>-<time> before PostgreSQL starts and init_db.py changes
   the tables; the copy is an ordinary backup that 「服务 → 从备份恢复」 accepts. Only the newest
   KEEP such copies are kept. Data last run by a newer client is not opened by an older one. */
const fs = require('node:fs');
const path = require('node:path');
const backup = require('./backup');

const RECORD = 'last-version.json';
const PREFIX = '升级前备份-';
const NAME = /^升级前备份-(.+)-(\d{8}-\d{6})$/;
const UNKNOWN = '未知版本';
const KEEP = 3;

// ---------------------------------------------------------------- versions (package.json "version")

const SEMVER = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;

function parseVersion(value) {
  const match = typeof value === 'string' ? SEMVER.exec(value) : null;
  if (!match) return null;
  return {core: match.slice(1, 4).map(Number), pre: match[4] ? match[4].split('.') : []};
}

/** -1, 0 or 1 as a is older than, the same as or newer than b (semantic versioning order); null if either is not a version. */
function compareVersions(a, b) {
  const [x, y] = [parseVersion(a), parseVersion(b)];
  if (!x || !y) return null;
  for (let i = 0; i < 3; i += 1) if (x.core[i] !== y.core[i]) return x.core[i] < y.core[i] ? -1 : 1;
  // A pre-release (1.0.0-beta.1) comes before the release itself.
  if (!x.pre.length || !y.pre.length) return x.pre.length === y.pre.length ? 0 : x.pre.length ? -1 : 1;
  for (let i = 0; i < Math.max(x.pre.length, y.pre.length); i += 1) {
    const [p, q] = [x.pre[i], y.pre[i]];
    if (p === undefined) return -1;
    if (q === undefined) return 1;
    if (p === q) continue;
    const [pn, qn] = [/^\d+$/.test(p), /^\d+$/.test(q)];
    if (pn && qn) return Number(p) < Number(q) ? -1 : 1;
    if (pn !== qn) return pn ? -1 : 1;
    return p < q ? -1 : 1;
  }
  return 0;
}

// ---------------------------------------------------------------- the record in the data folder

function readRecord(dataDir) {
  try {
    const value = JSON.parse(fs.readFileSync(path.join(dataDir, RECORD), 'utf8'));
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}

/** Written through a temporary file, so a crash cannot leave half a record behind. */
function writeRecord(dataDir, value) {
  const file = path.join(dataDir, RECORD);
  const temp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(value, null, 2));
  fs.renameSync(temp, file);
}

/** The client version the data in this folder belongs to, if it is known. */
function dataVersion(dataDir) {
  const record = readRecord(dataDir);
  return record && parseVersion(record.version) ? record.version : null;
}

/** After every successful start: this version has opened (and upgraded) the data. */
function recordStart(dataDir, {version, edition, now = new Date()}) {
  writeRecord(dataDir, {version, edition, updated_at: now.toISOString()});
}

/**
 * After 「从备份恢复」: the restored data is from this version or an older one (checkRestore), and the
 * backup it came from is itself the copy an upgrade backup would make, so the next start does not
 * make another one; it still upgrades the tables as usual.
 */
function recordRestore(dataDir, {version, edition, from, now = new Date()}) {
  writeRecord(dataDir, {version, edition, updated_at: now.toISOString(), restored_from: from ? path.basename(from) : null});
}

/** Refuse to restore a backup from a newer client: this client would then open newer data. */
function checkRestore(manifest, current) {
  const from = manifest && manifest.version;
  if (compareVersions(from, current) === 1) {
    throw new Error(`这份备份来自知页 ${from}，比当前的 ${current} 新，不能在旧版本中恢复。请安装 ${from} 或更新的版本后再恢复。`);
  }
}

function hasDatabase(dataDir) {
  return fs.existsSync(path.join(dataDir, 'database', 'PG_VERSION'));
}

/**
 * What a start of `current` has to do with this data folder:
 * fresh (no database yet), same, upgrade (previous is null when no version was recorded, as with
 * data last run by a client from before this record existed) or downgrade.
 */
function decide(dataDir, current) {
  if (!parseVersion(current)) throw new Error(`客户端版本号无效：${current}`);
  const record = readRecord(dataDir);
  const previous = record && parseVersion(record.version) ? record.version : null;
  if (!hasDatabase(dataDir)) return {kind: 'fresh', previous, record};
  if (!previous) return {kind: 'upgrade', previous: null, record};
  const order = compareVersions(previous, current);
  return {kind: order < 0 ? 'upgrade' : order > 0 ? 'downgrade' : 'same', previous, record};
}

// ---------------------------------------------------------------- upgrade backups

function folderName(previous, now) {
  return `${PREFIX}${previous || UNKNOWN}-${backup.stamp(now)}`;
}

/** A finished upgrade backup: a real folder (not a link) with this naming and a 知页 backup manifest. */
function isUpgradeBackup(folder) {
  try {
    return NAME.test(path.basename(folder)) && fs.lstatSync(folder).isDirectory() && Boolean(backup.readManifest(folder));
  } catch {
    return false;
  }
}

/** Finished upgrade backups directly inside the data folder, newest first. */
function listUpgradeBackups(dataDir) {
  let entries;
  try { entries = fs.readdirSync(dataDir, {withFileTypes: true}); } catch { return []; }
  return entries
    .filter(entry => entry.isDirectory() && NAME.test(entry.name))
    .map(entry => {
      const [, version, time] = NAME.exec(entry.name);
      return {name: entry.name, path: path.join(dataDir, entry.name), version, time};
    })
    .filter(item => isUpgradeBackup(item.path))
    .sort((a, b) => b.time.localeCompare(a.time) || b.name.localeCompare(a.name));
}

/**
 * Delete all but the newest `keep` upgrade backups; `current` (the one just made) always stays,
 * even if the clock was turned back. Each folder is checked again right before it is deleted.
 */
function pruneUpgradeBackups(dataDir, {keep = KEEP, current = null, log = () => {}} = {}) {
  const all = listUpgradeBackups(dataDir);
  const ordered = [...all.filter(b => b.name === current), ...all.filter(b => b.name !== current)];
  const removed = [];
  for (const old of ordered.slice(keep)) {
    if (path.resolve(path.dirname(old.path)) !== path.resolve(dataDir) || !isUpgradeBackup(old.path)) continue;
    try {
      backup.removeTree(old.path);
      removed.push(old.path);
      log(`已删除较早的升级前备份：${old.name}`);
    } catch (error) {
      log(`删除较早的升级前备份 ${old.name} 失败：${error.message}`);
    }
  }
  return removed;
}

/** The backup an earlier, unfinished attempt of this same upgrade already made, if it is still there. */
function pendingBackup(dataDir, record, previous, current) {
  const pending = record && record.pending;
  if (!pending || pending.to !== current || (pending.from || null) !== previous) return null;
  if (typeof pending.backup !== 'string' || !NAME.test(pending.backup)) return null;
  const folder = path.join(dataDir, pending.backup);
  return isUpgradeBackup(folder) ? folder : null;
}

const REASONS = {
  ENOSPC: '磁盘空间不足',
  EACCES: '没有读写数据目录中文件的权限',
  EPERM: '没有读写数据目录中文件的权限，或文件被其他程序占用',
  EBUSY: '文件被其他程序占用',
  EROFS: '数据目录所在的磁盘是只读的',
};

function backupFailed(error) {
  const reason = REASONS[error.code] ? `${REASONS[error.code]}（${error.code}）` : error.message;
  return Object.assign(new Error(`升级前备份没有完成：${reason}。为保护现有数据，没有继续升级。`), {
    title: '升级前备份没有完成',
    hint: '现有数据没有改动。请释放磁盘空间或关闭占用数据目录的程序后点「重试」；也可以先用菜单「服务 → 备份数据」备份到其他磁盘。',
    cause: error,
  });
}

function downgradeRefused(previous, current) {
  return Object.assign(new Error(`数据目录中的数据上次由知页 ${previous} 打开，比当前的 ${current} 新。旧版本可能读错或改坏新版本的数据，因此没有启动。`), {
    title: '数据来自更新的版本',
    hint: `请安装知页 ${previous} 或更新的版本后再打开。确实要使用 ${current}：可以用菜单「服务 → 从备份恢复」恢复由 ${current} 或更早版本做的备份（例如数据目录中的「升级前备份-…」文件夹，恢复后回到备份时的数据），或「更换数据目录」从空白数据开始。`,
    code: 'ZHIYE_DOWNGRADE',
  });
}

/**
 * Run before PostgreSQL starts. Returns {kind, previous, backup?, reused?}; throws when the backup
 * fails (nothing has been changed then) or when the data belongs to a newer client.
 *
 * @param {{dataDir: string, version: string, edition: string,
 *          ensureStopped?: () => Promise<void>, onBackup?: (info: {previous: string|null, version: string}) => void,
 *          log?: (text: string) => void, now?: Date, keep?: number}} options
 */
async function prepare({dataDir, version, edition, ensureStopped = async () => {}, onBackup = () => {}, log = () => {}, now = new Date(), keep = KEEP}) {
  const {kind, previous, record} = decide(dataDir, version);
  if (kind === 'fresh') {
    // Recorded at once: a first start that fails after creating the database is not an upgrade.
    recordStart(dataDir, {version, edition, now});
    return {kind, previous};
  }
  if (kind === 'same') return {kind, previous};
  if (kind === 'downgrade') throw downgradeRefused(previous, version);

  const from = previous ? `知页 ${previous} ` : '未记录版本的旧客户端';
  const earlier = pendingBackup(dataDir, record, previous, version);
  if (earlier) {
    log(`上次升级到 ${version} 没有完成，沿用那时的升级前备份：${earlier}`);
    return {kind, previous, backup: earlier, reused: true};
  }

  log(`数据上次由${from}打开，当前为 ${version}：先备份数据，再升级数据表。`);
  onBackup({previous, version});
  // Let the start-up screen show the line before the copy, which blocks, begins.
  await new Promise(resolve => setImmediate(resolve));
  const name = folderName(previous, now);
  const target = path.join(dataDir, name);
  try {
    await ensureStopped();
    if (fs.existsSync(target)) throw new Error(`${name} 已存在，请稍后重试`);
  } catch (error) {
    log(`升级前备份失败：${error.message}`);
    throw backupFailed(error);
  }
  try {
    backup.createBackup(dataDir, dataDir, {version: previous, edition, now, name});
  } catch (error) {
    log(`升级前备份失败：${error.stack || error.message}`);
    try { backup.removeTree(target); } catch (cleanup) { log(`清理未完成的备份 ${name} 失败：${cleanup.message}`); }
    throw backupFailed(error);
  }
  // Until this start succeeds the data still belongs to `previous`; a retry reuses this backup.
  writeRecord(dataDir, {version: previous, edition, updated_at: record && record.updated_at || null,
    pending: {from: previous, to: version, backup: name, created_at: now.toISOString()}});
  log(`升级前备份完成：${target}`);
  pruneUpgradeBackups(dataDir, {keep, current: name, log});
  return {kind, previous, backup: target, reused: false};
}

module.exports = {
  RECORD, PREFIX, KEEP, NAME, parseVersion, compareVersions, readRecord, writeRecord, dataVersion, recordStart,
  recordRestore, checkRestore, hasDatabase, decide, folderName, isUpgradeBackup, listUpgradeBackups,
  pruneUpgradeBackups, prepare,
};
