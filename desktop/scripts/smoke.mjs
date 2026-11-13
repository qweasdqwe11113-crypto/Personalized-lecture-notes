// Headless end-to-end check of the bundled runtime: start PostgreSQL and the service from a
// staged resources folder (or the repository with --dev), exercise the API, restart, and stop.
// Usage: node scripts/smoke.mjs [--dev] [--resources build/resources] [--edition release|demo]
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const require = createRequire(import.meta.url);
const {Runtime} = require('../src/runtime.js');
const {stagedResources, developmentResources} = require('../src/paths.js');
const {get} = require('../src/backend.js');
const {createBackup, checkBackup} = require('../src/backup.js');
const {shortPath} = require('../src/processes.js');

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, '..');
const args = process.argv.slice(2);
const option = (name, fallback) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : fallback; };
const edition = option('--edition', 'release') === 'demo' ? 'demo' : 'release';
const resources = args.includes('--dev') ? developmentResources(desktop)
  : stagedResources(path.resolve(desktop, option('--resources', 'build/resources')));
// A folder name outside ASCII, as a Chinese user name would give on Windows.
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), '知页 冒烟-'));
if (process.platform === 'win32') console.log(`PostgreSQL 使用的路径：${shortPath(dataDir)}`);

async function json(url, init) {
  const response = await fetch(url, init);
  const body = await response.json();
  return {status: response.status, body};
}

async function startAndCheck(round) {
  const runtime = new Runtime({resources, dataDir, edition, packaged: !args.includes('--dev')});
  runtime.on('step', s => console.log(`[${round}] ${s.label}: ${s.state}${s.detail ? ' · ' + s.detail : ''}`));
  try {
    const url = await runtime.start();
    const page = await get(url + '/');
    assert.equal(page.status, 200);
    assert.match(page.body, /知页/);
    const health = JSON.parse((await get(url + '/api/health')).body);
    assert.equal(health.database_ok, true);
    return {runtime, url};
  } catch (error) {
    console.error(runtime.log.tail(40).join('\n'));
    await runtime.stop().catch(() => {});
    throw error;
  }
}

let runtime;
try {
  ({runtime, url: globalThis.url} = await startAndCheck('first start'));
  const url = globalThis.url;
  const courses = (await json(url + '/api/courses')).body.courses;
  if (edition === 'release') {
    assert.deepEqual(courses, [], 'release starts without courses');
    assert.deepEqual((await json(url + '/api/course-tasks')).body.topics, {}, 'release lists no built-in lesson');
    assert.doesNotMatch((await get(url + '/')).body, /record-tab-experiments/);
  } else {
    assert.ok(courses.length >= 2, 'test edition carries the demonstration courses');
  }
  const created = await json(url + '/api/courses', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: '冒烟测试课程', stage: '大学'})});
  assert.equal(created.status, 201, JSON.stringify(created.body));
  await runtime.restartService();
  assert.equal((await json(runtime.url + '/api/courses')).body.courses.some(c => c.name === '冒烟测试课程'), true);
  await runtime.stop();

  const backups = fs.mkdtempSync(path.join(os.tmpdir(), 'zhiye-backup-'));
  const folder = createBackup(dataDir, backups, {version: 'smoke', edition});
  checkBackup(folder, dataDir, edition);
  fs.rmSync(backups, {recursive: true, force: true});

  ({runtime} = await startAndCheck('second start'));
  assert.equal((await json(runtime.url + '/api/courses')).body.courses.some(c => c.name === '冒烟测试课程'), true, 'data survives a restart');
  await runtime.stop();
  console.log(`smoke check passed (${edition}, ${process.platform})`);
} finally {
  if (runtime && runtime.state !== 'stopped') await runtime.stop().catch(() => {});
  if (!args.includes('--keep')) fs.rmSync(dataDir, {recursive: true, force: true});
}
