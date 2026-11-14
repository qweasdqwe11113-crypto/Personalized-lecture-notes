'use strict';
/* The bundled PostgreSQL: one private cluster in the data folder, listening on 127.0.0.1 with a
   random port and a random password, started with the client and stopped when it quits. */
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {cleanEnv, run, shortPath, WIN} = require('./processes');
const {freePort} = require('./ports');

const USER = 'zhiye';
const DATABASE = 'lecture_demo_prototype';
const CONF_MARK = '# 知页桌面客户端';
const CONF = [
  CONF_MARK,
  "listen_addresses = '127.0.0.1'",
  "unix_socket_directories = ''",
  'max_connections = 40',
  'shared_buffers = 64MB',
  '',
].join('\n');

function initdbArgs(dataDir, pwfile) {
  return ['-D', dataDir, '-U', USER, `--pwfile=${pwfile}`, '--auth=scram-sha-256',
    '--encoding=UTF8', '--locale=C', '--no-instructions'];
}

function readSecret(file) {
  try {
    const value = JSON.parse(fs.readFileSync(file, 'utf8'));
    return typeof value.password === 'string' && value.password.length >= 16 ? value.password : null;
  } catch {
    return null;
  }
}

function writeSecret(file, password) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.writeFileSync(file, JSON.stringify({user: USER, password}, null, 2), {mode: 0o600});
}

class Postgres {
  /** @param {{root: string, dataDir: string, secretFile: string, logFile: string, log: import('./logs').LogSink}} options */
  constructor({root, dataDir, secretFile, logFile, log}) {
    Object.assign(this, {root, dataDir, secretFile, logFile, log});
    this.port = null;
  }

  binary(name) {
    return path.join(this.root, 'bin', WIN ? `${name}.exe` : name);
  }

  env() {
    const lib = path.join(this.root, 'lib');
    const extra = {LC_MESSAGES: 'C'};
    if (process.platform === 'linux') extra.LD_LIBRARY_PATH = lib;
    return cleanEnv(extra);
  }

  async tool(name, args, options = {}) {
    const result = await run(this.binary(name), args, {env: this.env(), log: this.log, source: name, ...options});
    return result;
  }

  get initialized() {
    return fs.existsSync(path.join(this.dataDir, 'PG_VERSION'));
  }

  /** Create the cluster on first start. A half-created folder from an interrupted run is replaced. */
  async ensureCluster() {
    if (!fs.existsSync(this.binary('initdb'))) throw new Error('找不到随附的 PostgreSQL 程序，安装可能不完整，请重新安装。');
    if (this.initialized && readSecret(this.secretFile)) return false;
    if (this.initialized) throw new Error('数据库文件存在，但缺少它的访问密码文件 database-secret.json。请从备份恢复，或更换数据目录。');
    fs.rmSync(this.dataDir, {recursive: true, force: true});
    fs.mkdirSync(this.dataDir, {recursive: true});
    const password = crypto.randomBytes(24).toString('base64url');
    const pwfile = path.join(path.dirname(this.dataDir), `.pw-${process.pid}`);
    fs.writeFileSync(pwfile, password + '\n', {mode: 0o600});
    try {
      const {code, output} = await this.tool('initdb', initdbArgs(shortPath(this.dataDir), shortPath(pwfile)), {timeout: 180000});
      if (code !== 0) throw Object.assign(new Error('数据库初始化失败。'), {output});
    } finally {
      fs.rmSync(pwfile, {force: true});
    }
    fs.appendFileSync(path.join(this.dataDir, 'postgresql.conf'), '\n' + CONF);
    writeSecret(this.secretFile, password);
    return true;
  }

  async running() {
    if (!fs.existsSync(path.join(this.dataDir, 'postmaster.pid'))) return false;
    const {code} = await this.tool('pg_ctl', ['status', '-D', shortPath(this.dataDir)], {settle: 'exit', timeout: 30000});
    return code === 0;
  }

  async start() {
    // A server left behind by a crashed client may run on a port we no longer know: stop it first.
    if (await this.running()) await this.stop();
    const port = await freePort();
    fs.mkdirSync(path.dirname(this.logFile), {recursive: true});
    const {code, output} = await this.tool('pg_ctl', ['start', '-w', '-t', '90', '-D', shortPath(this.dataDir),
      '-l', shortPath(path.dirname(this.logFile)) + path.sep + path.basename(this.logFile), '-o', `-p ${port}`],
    {settle: 'exit', timeout: 120000});
    if (code !== 0) throw Object.assign(new Error('数据库没有启动成功。'), {output: output + this.serverLog()});
    this.port = port;
    return port;
  }

  async stop() {
    if (!fs.existsSync(path.join(this.dataDir, 'postmaster.pid'))) { this.port = null; return; }
    await this.tool('pg_ctl', ['stop', '-D', shortPath(this.dataDir), '-m', 'fast', '-w', '-t', '60'], {settle: 'exit', timeout: 90000});
    this.port = null;
  }

  /** The end of PostgreSQL's own log, for error reports. */
  serverLog(lines = 20) {
    try {
      return '\n' + fs.readFileSync(this.logFile, 'utf8').trimEnd().split('\n').slice(-lines).join('\n');
    } catch {
      return '';
    }
  }

  url() {
    const password = readSecret(this.secretFile);
    if (!this.port || !password) throw new Error('数据库尚未启动。');
    return `postgresql://${USER}:${encodeURIComponent(password)}@127.0.0.1:${this.port}/${DATABASE}`;
  }
}

module.exports = {Postgres, initdbArgs, readSecret, writeSecret, CONF, CONF_MARK, USER, DATABASE};
