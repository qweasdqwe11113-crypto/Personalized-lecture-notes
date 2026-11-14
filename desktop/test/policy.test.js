'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const policy = require('../src/policy');

test('only the exact service origin counts as the workspace', () => {
  const app = 'http://127.0.0.1:38660';
  assert.equal(policy.isAppUrl('http://127.0.0.1:38660/#workbench', app), true);
  assert.equal(policy.isAppUrl('http://127.0.0.1:38661/', app), false);
  assert.equal(policy.isAppUrl('http://localhost:38660/', app), false);
  assert.equal(policy.isAppUrl('https://127.0.0.1:38660/', app), false);
  assert.equal(policy.isAppUrl('http://127.0.0.1:38660/', null), false, 'nothing is trusted before the service runs');
  assert.equal(policy.isAppUrl('not a url', app), false);
});

test('the launcher matches with or without a hash or query', () => {
  const launcher = 'file:///opt/zhiye/resources/app.asar/src/launcher/index.html';
  assert.equal(policy.isLauncherUrl(launcher, launcher), true);
  assert.equal(policy.isLauncherUrl(`${launcher}#x`, launcher), true);
  assert.equal(policy.isLauncherUrl('file:///etc/passwd', launcher), false);
  assert.equal(policy.isLauncherUrl(undefined, launcher), false);
});

test('only web and mail links leave for the system browser', () => {
  assert.equal(policy.externalTarget('https://example.com/a?b=1'), 'https://example.com/a?b=1');
  assert.equal(policy.externalTarget('mailto:teacher@example.com'), 'mailto:teacher@example.com');
  for (const url of ['file:///etc/passwd', 'javascript:alert(1)', 'smb://server/share', 'ms-settings:', 'nonsense']) {
    assert.equal(policy.externalTarget(url), null, url);
  }
});

test('release tags compare as versions', () => {
  assert.equal(policy.isNewer('v0.2.0', '0.1.0'), true);
  assert.equal(policy.isNewer('v0.10.0', '0.9.9'), true);
  assert.equal(policy.isNewer('v1.0.0', '1.0.0'), false);
  assert.equal(policy.isNewer('v0.1.0', '0.2.0'), false);
  assert.equal(policy.isNewer(undefined, '0.1.0'), false);
});

test('pages may only use clipboard writing and full screen', () => {
  assert.deepEqual([...policy.ALLOWED_PERMISSIONS].sort(), ['clipboard-sanitized-write', 'fullscreen']);
});
