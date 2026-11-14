// Build the installer for this platform from build/resources (run scripts/stage.mjs first).
// Usage: node scripts/dist.mjs [--flavor release|demo] [--dir]
import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const require = createRequire(import.meta.url);
const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const flavor = args.includes('--flavor') && args[args.indexOf('--flavor') + 1] === 'demo' ? 'demo' : 'release';

const manifestFile = path.join(desktop, 'build', 'resources', 'manifest.json');
if (!fs.existsSync(manifestFile)) throw new Error('缺少 build/resources，请先运行 node scripts/stage.mjs。');
const manifest = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
if (manifest.flavor !== flavor) throw new Error(`build/resources 是 ${manifest.flavor} 版，请用 --flavor ${flavor} 重新运行 stage.mjs。`);
if (manifest.platform !== process.platform) throw new Error('build/resources 不是为当前系统准备的。');

const cli = require.resolve('electron-builder/cli.js');
const target = process.platform === 'win32' ? '--win' : '--linux';
const result = spawnSync(process.execPath, [cli, target, '--x64', '--publish', 'never', '--config', 'electron-builder.config.cjs',
  ...(args.includes('--dir') ? ['--dir'] : [])], {cwd: desktop, stdio: 'inherit', env: {...process.env, ZHIYE_FLAVOR: flavor}});
process.exit(result.status ?? 1);
