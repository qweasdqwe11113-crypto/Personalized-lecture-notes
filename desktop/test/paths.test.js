'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const paths = require('../src/paths');

test('unknown editions fall back to 正式版', () => {
  assert.equal(paths.edition('demo'), 'demo');
  for (const value of ['release', undefined, '', 'DEMO', 'test']) assert.equal(paths.edition(value), 'release');
});

test('the two editions and development runs keep separate data folders', () => {
  const names = [paths.dataFolderName('release'), paths.dataFolderName('demo'),
    paths.dataFolderName('release', true), paths.dataFolderName('demo', true)];
  assert.deepEqual(names, ['Zhiye', 'Zhiye-Test', 'Zhiye-Dev', 'Zhiye-Test-Dev']);
});

test('default data folder follows each platform convention', () => {
  assert.equal(paths.defaultDataDir('Zhiye', {platform: 'win32', env: {LOCALAPPDATA: 'C:\\Users\\林\\AppData\\Local'}, home: 'C:\\Users\\林'}),
    'C:\\Users\\林\\AppData\\Local\\Zhiye');
  assert.equal(paths.defaultDataDir('Zhiye', {platform: 'win32', env: {}, home: 'C:\\Users\\a'}), 'C:\\Users\\a\\AppData\\Local\\Zhiye');
  assert.equal(paths.defaultDataDir('Zhiye-Test', {platform: 'linux', env: {}, home: '/home/a'}), '/home/a/.local/share/zhiye-test');
  assert.equal(paths.defaultDataDir('Zhiye', {platform: 'linux', env: {XDG_DATA_HOME: '/data'}, home: '/home/a'}), '/data/zhiye');
});

test('staged resources point at the bundled interpreter for the platform', () => {
  assert.equal(paths.stagedResources('/opt/zhiye/resources', 'linux').python, path.join('/opt/zhiye/resources', 'python', 'bin', 'python3'));
  assert.equal(paths.stagedResources('R', 'win32').python, path.join('R', 'python', 'python.exe'));
  assert.equal(paths.stagedResources('R', 'linux').postgres, path.join('R', 'postgres'));
});

test('everything the client writes stays below the data folder', () => {
  const layout = paths.layout(path.join('x', 'Zhiye'));
  for (const [key, value] of Object.entries(layout)) {
    if (key !== 'root') assert.equal(path.dirname(value), layout.root, key);
  }
});
