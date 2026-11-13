'use strict';
/* Start-up and shutdown of everything behind the window, step by step, independent of Electron:
   the same code runs inside the client and in the headless smoke check used by CI. */
const fs = require('node:fs');
const path = require('node:path');
const {EventEmitter} = require('node:events');
const {LogSink} = require('./logs');
const {Postgres} = require('./postgres');
const {Backend} = require('./backend');
const {pickPort} = require('./ports');
const {layout} = require('./paths');
const upgrade = require('./upgrade');
const pkg = require('../package.json');

const STEPS = [
  {id: 'prepare', label: '检查数据目录'},
  {id: 'database', label: '启动本机数据库'},
  {id: 'migrate', label: '准备数据表'},
  {id: 'service', label: '启动教学服务'},
];

class Runtime extends EventEmitter {
  /**
   * @param {{resources: {python: string, backend: string, postgres: string}, dataDir: string,
   *          edition: 'release'|'demo', packaged: boolean, preferredPort?: number, version?: string}} options
   *        version: the client version, package.json "version" unless a test says otherwise.
   */
  constructor({resources, dataDir, edition, packaged, preferredPort, version = pkg.version}) {
    super();
    this.resources = resources;
    this.edition = edition;
    this.version = version;
    this.upgrade = null;
    this.preferredPort = preferredPort;
    this.dirs = layout(dataDir);
    this.log = new LogSink(path.join(this.dirs.logs, 'client.log'));
    this.postgres = new Postgres({root: resources.postgres, dataDir: this.dirs.database, secretFile: this.dirs.secret,
      logFile: path.join(this.dirs.logs, 'postgres.log'), log: this.log});
    this.backend = new Backend({python: resources.python, root: resources.backend, workspace: this.dirs.workspace,
      edition, packaged, log: this.log});
    this.backend.on('exit', info => {
      this.log.line('client', `服务意外退出（${info.signal || '退出码 ' + info.code}）。`);
      this.emit('service-exit', info);
    });
    this.state = 'idle';
  }

  get url() {
    return this.backend.url;
  }

  async step(id, work) {
    const {label} = STEPS.find(s => s.id === id);
    this.emit('step', {id, label, state: 'running'});
    this.log.line('client', `${label}…`);
    try {
      const detail = await work();
      this.emit('step', {id, label, state: 'done', detail});
      return detail;
    } catch (error) {
      this.log.line('client', `${label}失败：${error.message}`);
      if (error.output) this.log.line('client', String(error.output).trim().split('\n').slice(-15).join('\n'));
      this.emit('step', {id, label, state: 'failed', detail: error.message});
      throw Object.assign(error, {step: id, label});
    }
  }

  async start() {
    this.state = 'starting';
    try {
      await this.step('prepare', async () => {
        for (const dir of [this.dirs.root, this.dirs.workspace, this.dirs.logs]) fs.mkdirSync(dir, {recursive: true});
        fs.accessSync(this.dirs.root, fs.constants.W_OK);
        return this.checkVersion();
      });
      await this.step('database', async () => {
        const created = await this.postgres.ensureCluster();
        const port = await this.postgres.start();
        return created ? `首次运行，已创建数据库（端口 ${port}）` : `端口 ${port}`;
      });
      await this.step('migrate', () => this.backend.migrate(this.postgres.url()));
      await this.step('service', () => this.startService());
      this.state = 'running';
      this.recordVersion();
      return this.url;
    } catch (error) {
      this.state = 'failed';
      await this.stop().catch(() => {});
      throw error;
    }
  }

  /**
   * Before PostgreSQL starts: data last run by an older client is copied to 升级前备份-… first;
   * data from a newer client is refused. A failed backup stops the start (see upgrade.js).
   */
  async checkVersion() {
    let copying = false;
    this.upgrade = await upgrade.prepare({
      dataDir: this.dirs.root, version: this.version, edition: this.edition,
      ensureStopped: () => this.ensureDatabaseStopped(),
      log: text => this.log.line('client', text),
      onBackup: () => {
        copying = true;
        this.progress('prepare', '正在备份升级前的数据…');
      },
    });
    if (copying) this.emit('progress', null);
    const {kind, previous, reused} = this.upgrade;
    if (kind !== 'upgrade') return this.dirs.root;
    return reused ? '沿用本次升级已做的备份' : `已备份 ${previous || '旧版本'} 的数据`;
  }

  /** A server left behind by a crashed client still has the files open: stop it before they are copied. */
  async ensureDatabaseStopped() {
    if (!fs.existsSync(path.join(this.dirs.database, 'postmaster.pid'))) return;
    if (await this.postgres.running()) await this.postgres.stop();
    if (await this.postgres.running()) throw new Error('上次没有正常关闭的数据库仍在运行，无法安全复制');
  }

  /** Remember that this version has opened the data; the next start compares against it. */
  recordVersion() {
    const {kind, previous, backup} = this.upgrade || {};
    try {
      upgrade.recordStart(this.dirs.root, {version: this.version, edition: this.edition});
      if (kind === 'upgrade') this.log.line('client', `已从 ${previous || '未记录的版本'} 升级到 ${this.version}；升级前的数据在 ${backup}`);
    } catch (error) {
      this.log.line('client', `记录客户端版本失败：${error.message}`);
    }
  }

  /** One line on the start-up screen while a long part of a step runs; null when it is over. */
  progress(id, text) {
    const {label} = STEPS.find(s => s.id === id);
    this.emit('step', {id, label, state: 'running', detail: text});
    this.emit('progress', text);
  }

  async startService() {
    const {port, preferred} = await pickPort(this.preferredPort);
    if (!preferred && this.preferredPort) this.log.line('client', `端口 ${this.preferredPort} 已被占用，改用 ${port}；页面上记住的偏好会重新开始。`);
    return this.backend.start(this.postgres.url(), port);
  }

  /** Apply saved model settings: only the Python service restarts, the database keeps running. */
  async restartService() {
    this.state = 'restarting';
    await this.backend.stop();
    try {
      await this.step('service', () => this.startService());
      this.state = 'running';
      return this.url;
    } catch (error) {
      this.state = 'failed';
      throw error;
    }
  }

  async stop() {
    this.state = 'stopping';
    await this.backend.stop();
    await this.postgres.stop().catch(error => this.log.line('client', `数据库停止时出错：${error.message}`));
    this.log.flush();
    this.state = 'stopped';
  }
}

module.exports = {Runtime, STEPS};
