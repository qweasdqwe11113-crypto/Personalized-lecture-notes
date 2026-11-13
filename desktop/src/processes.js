'use strict';
/* Child processes: run a helper to completion, stop a long-running one with its children, and
   build a clean environment so settings from the user's shell cannot leak into the service. */
const fs = require('node:fs');
const {spawn, spawnSync} = require('node:child_process');

const WIN = process.platform === 'win32';
// Variables the service or PostgreSQL would read as their own configuration.
const OWNED = /^(QWEN_|VLM_|PPT_AESTHETIC_|MODEL_CONCURRENCY$|DATABASE_URL$|PORT$|ZHIYE_|PYTHON|PG[A-Z]|LD_LIBRARY_PATH$|FLASK_)/i;

function cleanEnv(extra = {}, base = process.env) {
  const env = {};
  for (const [key, value] of Object.entries(base)) if (!OWNED.test(key)) env[key] = value;
  return {...env, ...extra};
}

/**
 * Run a command and collect its output. settle='exit' resolves when the process itself ends:
 * pg_ctl leaves the server running and that server may hold the output pipes open.
 */
function run(command, args, {cwd, env, log, source = 'run', timeout = 120000, settle = 'close'} = {}) {
  return new Promise((resolve, reject) => {
    let output = '', done = false;
    const child = spawn(command, args, {cwd, env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true});
    const take = chunk => {
      const text = chunk.toString('utf8');
      output = (output + text).slice(-65536);
      if (log) log.write(source, text);
    };
    child.stdout.on('data', take);
    child.stderr.on('data', take);
    const timer = setTimeout(() => {
      if (done) return;
      done = true;
      killTree(child);
      reject(Object.assign(new Error(`${source} 超过 ${Math.round(timeout / 1000)} 秒仍未完成。`), {output}));
    }, timeout);
    child.once('error', error => {
      if (done) return;
      done = true; clearTimeout(timer);
      reject(Object.assign(error, {output}));
    });
    child.once(settle, code => {
      if (done) return;
      done = true; clearTimeout(timer);
      if (log) log.flush();
      resolve({code, output});
    });
  });
}

/** Stop a process and everything it started; resolves once it has exited. */
function killTree(child, grace = 5000) {
  return new Promise(resolve => {
    if (!child || child.exitCode !== null || child.signalCode !== null) return resolve();
    child.once('exit', () => resolve());
    if (WIN) {
      spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], {windowsHide: true, stdio: 'ignore'});
      return;
    }
    child.kill('SIGTERM');
    setTimeout(() => { if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL'); }, grace).unref();
  });
}

/**
 * On Windows, PostgreSQL reads paths in the ANSI code page; a folder below a user name it cannot
 * represent becomes unreadable. The 8.3 short form of an existing path is plain ASCII.
 */
function shortPath(target) {
  if (!WIN || plainPath(target) || /["%^!]/.test(target)) return target;
  // Arguments go to cmd verbatim: Node would otherwise escape the inner quotes and cmd would
  // read them as part of the path. /u makes cmd write UTF-16, so the console code page cannot
  // turn characters into "?".
  const result = spawnSync('cmd.exe', ['/d', '/u', '/s', '/c', `"for %I in ("${target}") do @echo %~sI"`],
    {windowsHide: true, windowsVerbatimArguments: true, timeout: 10000});
  const value = result.stdout ? result.stdout.toString('utf16le').trim().split(/\r?\n/).pop().trim() : '';
  // Where the volume keeps no 8.3 names the long path comes back unchanged; then there is nothing
  // better to give PostgreSQL than the original.
  return plainPath(value) && fs.existsSync(value) ? value : target;
}

/** Printable ASCII without "?", which is what a lost character turns into. */
function plainPath(value) {
  return /^[\x20-\x3e\x40-\x7e]+$/.test(value);
}

module.exports = {cleanEnv, run, killTree, shortPath, plainPath, WIN};
