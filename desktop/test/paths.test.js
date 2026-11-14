'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const paths = require('../src/paths');

test('runs from source keep their data apart from the installed client', () => {
  assert.deepEqual([paths.dataFolderName(), paths.dataFolderName(true)], ['Zhiye', 'Zhiye-Dev']);
});

test('default data folder follows each platform convention', () => {
  assert.equal(paths.defaultDataDir('Zhiye', {platform: 'win32', env: {LOCALAPPDATA: 'C:\\Users\\林\\AppData\\Local'}, home: 'C:\\Users\\林'}),
    'C:\\Users\\林\\AppData\\Local\\Zhiye');
  assert.equal(paths.defaultDataDir('Zhiye', {platform: 'win32', env: {}, home: 'C:\\Users\\a'}), 'C:\\Users\\a\\AppData\\Local\\Zhiye');
  assert.equal(paths.defaultDataDir('Zhiye-Dev', {platform: 'linux', env: {}, home: '/home/a'}), '/home/a/.local/share/zhiye-dev');
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
