'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const sandbox = require('../scripts/linux-sandbox.cjs');

const missing = path.join(os.tmpdir(), 'no-such-chrome-sandbox');
const ok = () => ({status: 0});
const blocked = () => ({status: 1});
const noUnshare = () => ({error: new Error('ENOENT')});

test('the sandbox is dropped only on Linux, and only where namespaces are blocked', () => {
  assert.equal(sandbox.needsNoSandbox(missing, {platform: 'win32', probe: blocked}), false);
  assert.equal(sandbox.needsNoSandbox(missing, {platform: 'linux', probe: ok}), false);
  assert.equal(sandbox.needsNoSandbox(missing, {platform: 'linux', probe: blocked}), true);
});

test('without unshare the kernel switches decide', () => {
  const switches = values => file => values[path.basename(file)] ?? null;
  assert.equal(sandbox.namespacesUsable({probe: noUnshare, readFile: switches({})}), true);
  assert.equal(sandbox.namespacesUsable({probe: noUnshare, readFile: switches({apparmor_restrict_unprivileged_userns: '1'})}), false);
  assert.equal(sandbox.namespacesUsable({probe: noUnshare, readFile: switches({unprivileged_userns_clone: '0'})}), false);
  assert.equal(sandbox.namespacesUsable({probe: noUnshare, readFile: switches({max_user_namespaces: '0'})}), false);
});

test('an ordinary chrome-sandbox file does not count as the setuid helper', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'zhiye-sandbox-'));
  try {
    const helper = path.join(dir, 'chrome-sandbox');
    fs.writeFileSync(helper, '');
    assert.equal(sandbox.helperReady(helper), false);
    assert.equal(sandbox.helperReady(missing), false);
  } finally { fs.rmSync(dir, {recursive: true, force: true}); }
});

test('the Linux start script passes the arguments on, adding --no-sandbox only where needed', {skip: process.platform === 'win32'}, () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'zhiye-launcher-'));
  try {
    const app = path.join(dir, 'app'), fake = path.join(dir, 'fake');
    fs.mkdirSync(app); fs.mkdirSync(fake);
    fs.writeFileSync(path.join(app, 'zhiye-bin'), '#!/bin/sh\necho "$@"\n', {mode: 0o755});
    fs.writeFileSync(path.join(app, 'zhiye'), sandbox.launcher('zhiye-bin'), {mode: 0o755});
    fs.writeFileSync(path.join(app, 'chrome-sandbox'), '');
    // Started through a symbolic link, as a link in ~/.local/bin would be.
    fs.symlinkSync(path.join(app, 'zhiye'), path.join(dir, 'zhiye-link'));
    const run = unshareStatus => {
      fs.writeFileSync(path.join(fake, 'unshare'), `#!/bin/sh\nexit ${unshareStatus}\n`, {mode: 0o755});
      const result = spawnSync(path.join(dir, 'zhiye-link'), ['--flag', '文件 名'], {
        encoding: 'utf8', env: {...process.env, PATH: `${fake}:${process.env.PATH}`},
      });
      assert.equal(result.status, 0, result.stderr);
      return result.stdout.trim();
    };
    assert.equal(run(0), '--flag 文件 名');
    assert.equal(run(1), '--no-sandbox --flag 文件 名');
  } finally { fs.rmSync(dir, {recursive: true, force: true}); }
});
