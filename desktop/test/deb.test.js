'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

const desktop = path.resolve(__dirname, '..');
const configFile = path.join(desktop, 'electron-builder.config.cjs');

/** The installer settings as electron-builder sees them for one edition. */
function builderConfig(flavor) {
  const before = process.env.ZHIYE_FLAVOR;
  process.env.ZHIYE_FLAVOR = flavor;
  delete require.cache[configFile];
  try { return require(configFile); } finally {
    if (before === undefined) delete process.env.ZHIYE_FLAVOR; else process.env.ZHIYE_FLAVOR = before;
    delete require.cache[configFile];
  }
}

/** What electron-builder 26 (FpmTarget.writeConfigFile) does to the template: unknown macros are an error. */
function expand(template, config) {
  const values = {executable: config.executableName, sanitizedProductName: config.productName,
    productFilename: config.executableName, ...config.linux};
  return template.replace(/\${([a-zA-Z]+)}/g, (match, name) => {
    if (!(name in values)) throw new Error(`Macro ${name} is not defined`);
    return values[name];
  });
}

test('both editions build a .deb under their own package name, so they install side by side', () => {
  const release = builderConfig('release'), demo = builderConfig('demo');
  for (const config of [release, demo]) {
    assert.ok(config.linux.target.includes('deb'));
    assert.equal(config.deb.packageName, config.executableName);
    assert.ok(config.linux.maintainer, 'without a maintainer electron-builder asks for an author email');
  }
  assert.deepEqual([release.deb.packageName, demo.deb.packageName], ['zhiye', 'zhiye-test']);
  assert.notEqual(release.productName, demo.productName, 'the install folder under /opt comes from the product name');
  assert.ok(release.deb.recommends.includes('fonts-noto-cjk'), 'the Chinese interface needs CJK fonts');
});

test('PostgreSQL is packaged once, from build/resources, not again from node_modules', () => {
  const config = builderConfig('release');
  assert.ok(config.extraResources.some(entry => entry.from === 'build/resources/postgres'));
  assert.ok(config.files.includes('!node_modules/@embedded-postgres{,/**/*}'));
});

test('the after-install script expands cleanly and always makes chrome-sandbox setuid root', {skip: process.platform === 'win32'}, () => {
  for (const flavor of ['release', 'demo']) {
    const config = builderConfig(flavor);
    const script = expand(fs.readFileSync(path.join(desktop, config.deb.afterInstall), 'utf8'), config);
    const folder = `/opt/${config.productName}`;
    assert.ok(script.includes(`chown root:root '${folder}/chrome-sandbox'`), flavor);
    assert.ok(script.includes(`chmod 4755 '${folder}/chrome-sandbox'`), flavor);
    assert.ok(!script.includes('chmod 0755'), 'no branch leaves the helper without the setuid bit');
    // The /usr/bin link goes to the start script, not to the program behind it.
    assert.ok(script.includes(`'/usr/bin/${config.executableName}' '${config.executableName}' '${folder}/${config.executableName}' 100`), flavor);
    const syntax = spawnSync('bash', ['-n'], {input: script, encoding: 'utf8'});
    assert.equal(syntax.status, 0, syntax.stderr);
  }
});
