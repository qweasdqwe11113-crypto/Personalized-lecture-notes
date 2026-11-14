// Run the client from source (npm run dev): the repository's .venv and code, PostgreSQL from
// node_modules, its own data folder (Zhiye-Dev) that starts empty like the installed client's.
import {spawn} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import electron from 'electron';
import sandbox from './linux-sandbox.cjs';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
// Ubuntu 24.04+ blocks Chromium's sandbox for programs without an AppArmor profile; see linux-sandbox.cjs.
const args = sandbox.needsNoSandbox(path.join(path.dirname(electron), 'chrome-sandbox')) ? ['--no-sandbox', '.'] : ['.'];
const child = spawn(electron, args, {cwd: desktop, stdio: 'inherit'});
child.on('exit', code => process.exit(code ?? 0));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
