// Collect what the installer carries into build/resources:
//   python/    a portable CPython with requirements.txt installed
//   backend/   the service code (an allow-list: no tests, experiments or, for 正式版, demonstration data)
//   postgres/  the portable PostgreSQL for this platform
// Usage: node scripts/stage.mjs [--flavor release|demo]
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {pruneStdlib} from './prune.mjs';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(desktop, '..');
const out = path.join(desktop, 'build', 'resources');
const cache = path.join(desktop, '.cache');
const args = process.argv.slice(2);
const flavor = args.includes('--flavor') && args[args.indexOf('--flavor') + 1] === 'demo' ? 'demo' : 'release';
const WIN = process.platform === 'win32';

// python-build-standalone 20261021, CPython 3.12.15, stripped "install only" builds.
const PYTHON = {
  linux: {
    url: 'https://github.com/astral-sh/python-build-standalone/releases/download/20261021/cpython-3.12.15%2B20261021-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz',
    sha256: '6d7521138fd33afddc1ccd08abb007d9a2829e38117c2576f9efdc0ec73ce772',
  },
  win32: {
    url: 'https://github.com/astral-sh/python-build-standalone/releases/download/20261021/cpython-3.12.15%2B20261021-x86_64-pc-windows-msvc-install_only_stripped.tar.gz',
    sha256: 'aac5d331fc3307343f95928fdbea4583f0d53973d08c7e1d081884fbd229a0e1',
  },
};

// The service reads only these; everything else in the repository stays out of the installer.
const BACKEND = ['demo/prompts', 'demo/static', 'demo/templates', 'assets/ppt-templates', 'tools/ppt-kit/pptkit'];
const BACKEND_FILES = /^(.+\.py|schema\.sql|render_ppt_slides\.ps1)$/;
const DEMO_DATA = ['demo/seed.json', 'demo/university_seed.json', 'demo/feedback_issues.json'];

function log(message) { console.log(`[stage] ${message}`); }
const sha256 = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

async function download(url, file, expected) {
  if (fs.existsSync(file) && sha256(file) === expected) return file;
  fs.mkdirSync(path.dirname(file), {recursive: true});
  log(`下载 ${path.basename(decodeURIComponent(url))}`);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`下载失败：${response.status} ${url}`);
  fs.writeFileSync(file, Buffer.from(await response.arrayBuffer()));
  const actual = sha256(file);
  if (actual !== expected) { fs.rmSync(file); throw new Error(`校验值不符：${actual}`); }
  return file;
}

function pythonBinary(root) {
  return WIN ? path.join(root, 'python.exe') : path.join(root, 'bin', 'python3');
}

async function stagePython() {
  const spec = PYTHON[process.platform];
  if (!spec) throw new Error(`暂不支持在 ${process.platform} 上打包。`);
  const requirements = fs.readFileSync(path.join(repo, 'requirements.txt'));
  const marker = path.join(out, 'python', '.zhiye-requirements');
  // The pruning rules are part of the marker, so changing them reinstalls Python.
  const want = crypto.createHash('sha256').update(spec.sha256).update(requirements).update(pruneStdlib.toString()).digest('hex');
  if (fs.existsSync(marker) && fs.readFileSync(marker, 'utf8') === want) return log('Python 运行环境已是最新，跳过');
  const archive = await download(spec.url, path.join(cache, decodeURIComponent(path.basename(spec.url))), spec.sha256);
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'zhiye-python-'));
  try {
    log('解压 Python');
    // On Windows use the system's bsdtar: Git's GNU tar reads a "D:" path as a remote host.
    const tar = WIN ? path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'tar.exe') : 'tar';
    execFileSync(tar, ['-xzf', archive, '-C', temp], {stdio: 'inherit'});
    fs.rmSync(path.join(out, 'python'), {recursive: true, force: true});
    fs.mkdirSync(out, {recursive: true});
    fs.cpSync(path.join(temp, 'python'), path.join(out, 'python'), {recursive: true, verbatimSymlinks: true});
  } finally {
    fs.rmSync(temp, {recursive: true, force: true});
  }
  const python = pythonBinary(path.join(out, 'python'));
  log('安装依赖（requirements.txt）');
  execFileSync(python, ['-m', 'pip', 'install', '--no-cache-dir', '--disable-pip-version-check', '--no-warn-script-location',
    '--only-binary=:all:', '-r', path.join(repo, 'requirements.txt')], {stdio: 'inherit', env: {...process.env, PYTHONNOUSERSITE: '1'}});
  // pip is only needed while building.
  execFileSync(python, ['-m', 'pip', 'uninstall', '-y', '--disable-pip-version-check', 'pip'], {stdio: 'inherit'});
  pruneStdlib(path.join(out, 'python'), WIN);
  fs.writeFileSync(marker, want);
}

function copyTree(from, to, keep = () => true) {
  for (const entry of fs.readdirSync(from, {withFileTypes: true})) {
    if (entry.name === '__pycache__' || entry.name === '.DS_Store') continue;
    const source = path.join(from, entry.name), target = path.join(to, entry.name);
    if (entry.isDirectory()) copyTree(source, target, keep);
    else if (keep(entry.name)) { fs.mkdirSync(to, {recursive: true}); fs.copyFileSync(source, target); }
  }
}

function stageBackend() {
  const target = path.join(out, 'backend');
  fs.rmSync(target, {recursive: true, force: true});
  for (const name of fs.readdirSync(path.join(repo, 'demo'))) {
    if (BACKEND_FILES.test(name)) { fs.mkdirSync(path.join(target, 'demo'), {recursive: true}); fs.copyFileSync(path.join(repo, 'demo', name), path.join(target, 'demo', name)); }
  }
  for (const dir of BACKEND) copyTree(path.join(repo, dir), path.join(target, dir));
  if (flavor === 'demo') for (const file of DEMO_DATA) fs.copyFileSync(path.join(repo, file), path.join(target, file));
  log(`复制服务代码（${flavor === 'demo' ? '测试版，含演示数据' : '正式版，不含演示数据'}）`);
  // Compile now: the installed program folder may be read-only at run time.
  execFileSync(pythonBinary(path.join(out, 'python')), ['-m', 'compileall', '-q', '-j', '0', path.join(target, 'demo'), path.join(target, 'tools')], {stdio: 'inherit'});
}

/**
 * Shared libraries come as libX.so -> libX.so.N -> libX.so.N.M. Programs load libX.so.N only:
 * keep that name as the one real file and drop the other two, so nothing depends on symlinks
 * surviving the installer and no library is stored three times.
 */
function flattenLibraries(dir) {
  if (!fs.existsSync(dir)) return;
  const links = fs.readdirSync(dir).filter(name => fs.lstatSync(path.join(dir, name)).isSymbolicLink());
  for (const name of links.filter(n => /\.so$/.test(n))) fs.unlinkSync(path.join(dir, name));
  for (const name of links.filter(n => !/\.so$/.test(n))) {
    const link = path.join(dir, name);
    const real = fs.realpathSync(link);
    fs.unlinkSync(link);
    fs.renameSync(real, link);
  }
}

/**
 * The package lists its library links in pg-symlinks.json and recreates them in an install script,
 * which some npm setups skip. Recreate any that are missing so staging gives the same result.
 */
function restoreLinks(native, root) {
  const file = path.join(native, 'pg-symlinks.json');
  if (!fs.existsSync(file)) return;
  const at = p => path.join(root, path.relative('native', p));
  for (const {source, target} of JSON.parse(fs.readFileSync(file, 'utf8'))) {
    const link = at(target), real = at(source);
    if (fs.existsSync(link) || !fs.existsSync(real)) continue;
    fs.symlinkSync(path.relative(path.dirname(link), real), link);
  }
}

function stagePostgres() {
  const key = WIN ? 'windows' : process.platform === 'darwin' ? 'darwin' : 'linux';
  const native = path.join(desktop, 'node_modules', '@embedded-postgres', `${key}-x64`, 'native');
  if (!fs.existsSync(path.join(native, 'bin'))) throw new Error(`缺少 @embedded-postgres/${key}-x64，请在 desktop 目录运行 npm install。`);
  const target = path.join(out, 'postgres');
  fs.rmSync(target, {recursive: true, force: true});
  fs.cpSync(native, target, {recursive: true, verbatimSymlinks: true,
    filter: source => !/(^|[\\/])(include|pkgconfig)$|\.(a|lib|pdb)$|[\\/]wx[^\\/]*\.dll$|pg-symlinks\.json$/i.test(source)});
  if (!WIN) restoreLinks(native, target);
  flattenLibraries(path.join(target, 'lib'));
  log('复制 PostgreSQL');
}

await stagePython();
stageBackend();
stagePostgres();
const python = pythonBinary(path.join(out, 'python'));
fs.writeFileSync(path.join(out, 'manifest.json'), JSON.stringify({
  flavor, platform: process.platform,
  python: execFileSync(python, ['-c', 'import platform; print(platform.python_version())'], {encoding: 'utf8'}).trim(),
  created_at: new Date().toISOString(),
}, null, 2));
log(`完成：${out}`);
