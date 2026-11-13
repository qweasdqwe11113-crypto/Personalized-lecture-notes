// Run the client from source: the repository's .venv and code, PostgreSQL from node_modules.
// npm run dev         正式版（空库）
// npm run dev:test    测试版（带演示课程）
import {spawn} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import electron from 'electron';
import sandbox from './linux-sandbox.cjs';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const edition = process.argv.includes('--edition') ? process.argv[process.argv.indexOf('--edition') + 1] : 'release';
// Ubuntu 24.04+ blocks Chromium's sandbox for programs without an AppArmor profile; see linux-sandbox.cjs.
const args = sandbox.needsNoSandbox(path.join(path.dirname(electron), 'chrome-sandbox')) ? ['--no-sandbox', '.'] : ['.'];
const child = spawn(electron, args, {cwd: desktop, stdio: 'inherit', env: {...process.env, ZHIYE_DESKTOP_EDITION: edition}});
child.on('exit', code => process.exit(code ?? 0));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
