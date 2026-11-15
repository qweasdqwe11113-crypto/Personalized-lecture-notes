'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const config = require('../src/config');

const temp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'zhiye-config-'));

test('missing or damaged settings give the defaults', () => {
  const dir = temp();
  try {
    assert.deepEqual(config.load(path.join(dir, 'none.json')), {...config.DEFAULTS});
    for (const text of ['{', '[]', 'null', '"x"']) {
      fs.writeFileSync(path.join(dir, 'bad.json'), text);
      assert.deepEqual(config.load(path.join(dir, 'bad.json')), {...config.DEFAULTS}, text);
    }
  } finally { fs.rmSync(dir, {recursive: true, force: true}); }
});

test('settings survive a save and load, and invalid fields are dropped', () => {
  const dir = temp(), file = path.join(dir, 'nested', 'desktop.json');
  try {
    const bounds = {x: 10, y: 20, width: 1200, height: 800, maximized: true};
    const saved = {dataDir: '/data/知页', bounds, lastVersion: '0.1.0', autoCheckUpdates: false, skippedVersion: 'v0.2.0'};
    config.save(file, saved);
    assert.deepEqual(config.load(file), saved);
    assert.deepEqual(fs.readdirSync(path.dirname(file)), ['desktop.json'], 'no temporary file left behind');
    fs.writeFileSync(file, JSON.stringify({dataDir: '', bounds: {x: 0, y: 0, width: 50, height: 50}, lastVersion: 3, skippedVersion: 2}));
    assert.deepEqual(config.load(file), {...config.DEFAULTS});
  } finally { fs.rmSync(dir, {recursive: true, force: true}); }
});

test('the start-up update check stays on unless it was turned off', () => {
  const dir = temp(), file = path.join(dir, 'desktop.json');
  try {
    // A settings file from before the option existed.
    fs.writeFileSync(file, JSON.stringify({dataDir: null, bounds: null, lastVersion: '0.1.0'}));
    assert.equal(config.load(file).autoCheckUpdates, true);
    for (const value of [null, 0, 'no', true]) {
      fs.writeFileSync(file, JSON.stringify({autoCheckUpdates: value}));
      assert.equal(config.load(file).autoCheckUpdates, true, String(value));
    }
    fs.writeFileSync(file, JSON.stringify({autoCheckUpdates: false}));
    assert.equal(config.load(file).autoCheckUpdates, false);
  } finally { fs.rmSync(dir, {recursive: true, force: true}); }
});

test('window bounds must be numbers of a usable size', () => {
  assert.equal(config.validBounds({x: 0, y: 0, width: 960, height: 640}), true);
  assert.equal(config.validBounds({x: 0, y: 0, width: 300, height: 640}), false);
  assert.equal(config.validBounds({x: 'a', y: 0, width: 960, height: 640}), false);
  assert.equal(config.validBounds(null), false);
});
