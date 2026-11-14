'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

const desktop = path.resolve(__dirname, '..');
const configFile = path.join(desktop, 'electron-builder.config.cjs');

/** The installer settings as electron-builder sees them. */
function builderConfig() {
  delete require.cache[configFile];
  try { return require(configFile); } finally { delete require.cache[configFile]; }
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

test('Linux gets a .deb named zhiye, installed in /opt/知页', () => {
  const config = builderConfig();
  assert.ok(config.linux.target.includes('deb'));
  assert.equal(config.deb.packageName, 'zhiye');
  assert.equal(config.deb.packageName, config.executableName);
  assert.equal(config.productName, '知页', 'the install folder under /opt comes from the product name');
  assert.ok(config.linux.maintainer, 'without a maintainer electron-builder asks for an author email');
  assert.ok(config.deb.recommends.includes('fonts-noto-cjk'), 'the Chinese interface needs CJK fonts');
});

test('PostgreSQL is packaged once, from build/resources, not again from node_modules', () => {
  const config = builderConfig();
  assert.ok(config.extraResources.some(entry => entry.from === 'build/resources/postgres'));
  assert.ok(config.files.includes('!node_modules/@embedded-postgres{,/**/*}'));
});

test('the after-install script expands cleanly and always makes chrome-sandbox setuid root', {skip: process.platform === 'win32'}, () => {
  const config = builderConfig();
  const script = expand(fs.readFileSync(path.join(desktop, config.deb.afterInstall), 'utf8'), config);
  const folder = `/opt/${config.productName}`;
  assert.ok(script.includes(`chown root:root '${folder}/chrome-sandbox'`));
  assert.ok(script.includes(`chmod 4755 '${folder}/chrome-sandbox'`));
  assert.ok(!script.includes('chmod 0755'), 'no branch leaves the helper without the setuid bit');
  // The /usr/bin link goes to the start script, not to the program behind it.
  assert.ok(script.includes(`'/usr/bin/${config.executableName}' '${config.executableName}' '${folder}/${config.executableName}' 100`));
  const syntax = spawnSync('bash', ['-n'], {input: script, encoding: 'utf8'});
  assert.equal(syntax.status, 0, syntax.stderr);
});
