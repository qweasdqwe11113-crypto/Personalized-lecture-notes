'use strict';
/* Where the bundled programs and the user's data live. Pure functions, so tests and the
   headless smoke check can use them without Electron. */
const path = require('node:path');
const os = require('node:os');

const EDITIONS = {
  release: {label: '正式版', folder: 'Zhiye'},
  demo: {label: '测试版', folder: 'Zhiye-Test'},
};

function edition(value) {
  return value === 'demo' ? 'demo' : 'release';
}

function pythonIn(root, platform = process.platform) {
  return platform === 'win32' ? path.join(root, 'python.exe') : path.join(root, 'bin', 'python3');
}

/** Programs inside a staged or packaged resources folder (python/, backend/, postgres/). */
function stagedResources(dir, platform = process.platform) {
  return {
    python: pythonIn(path.join(dir, 'python'), platform),
    backend: path.join(dir, 'backend'),
    postgres: path.join(dir, 'postgres'),
  };
}

/** During development: the repository's virtualenv and code, PostgreSQL from node_modules. */
function developmentResources(desktopDir, platform = process.platform) {
  const repo = path.resolve(desktopDir, '..');
  const venv = path.join(repo, '.venv');
  return {
    python: platform === 'win32' ? path.join(venv, 'Scripts', 'python.exe') : path.join(venv, 'bin', 'python'),
    backend: repo,
    postgres: path.join(desktopDir, 'node_modules', '@embedded-postgres', `${platformKey(platform)}-x64`, 'native'),
  };
}

function platformKey(platform = process.platform) {
  return platform === 'win32' ? 'windows' : platform === 'darwin' ? 'darwin' : 'linux';
}

/**
 * Default data folder. Local (not roaming) application data on Windows, XDG data on Linux.
 * The folder name is ASCII: PostgreSQL on Windows reads its arguments in the ANSI code page.
 */
function defaultDataDir(name, {platform = process.platform, env = process.env, home = os.homedir()} = {}) {
  if (platform === 'win32') return path.win32.join(env.LOCALAPPDATA || path.win32.join(home, 'AppData', 'Local'), name);
  if (platform === 'darwin') return path.posix.join(home, 'Library', 'Application Support', name);
  return path.posix.join(env.XDG_DATA_HOME || path.posix.join(home, '.local', 'share'), name.toLowerCase());
}

function dataFolderName(kind, development = false) {
  return EDITIONS[edition(kind)].folder + (development ? '-Dev' : '');
}

/** Everything the client writes, below one data folder. */
function layout(dataDir) {
  return {
    root: dataDir,
    database: path.join(dataDir, 'database'),
    workspace: path.join(dataDir, 'workspace'),
    logs: path.join(dataDir, 'logs'),
    secret: path.join(dataDir, 'database-secret.json'),
  };
}

module.exports = {EDITIONS, edition, stagedResources, developmentResources, platformKey, defaultDataDir, dataFolderName, layout, pythonIn};
