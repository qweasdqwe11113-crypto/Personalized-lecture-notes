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

const STEPS = [
  {id: 'prepare', label: '检查数据目录'},
  {id: 'database', label: '启动本机数据库'},
  {id: 'migrate', label: '准备数据表'},
  {id: 'service', label: '启动教学服务'},
];

class Runtime extends EventEmitter {
  /**
   * @param {{resources: {python: string, backend: string, postgres: string}, dataDir: string,
   *          edition: 'release'|'demo', packaged: boolean, preferredPort?: number}} options
   */
  constructor({resources, dataDir, edition, packaged, preferredPort}) {
    super();
    this.resources = resources;
    this.edition = edition;
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
        return this.dirs.root;
      });
      await this.step('database', async () => {
        const created = await this.postgres.ensureCluster();
        const port = await this.postgres.start();
        return created ? `首次运行，已创建数据库（端口 ${port}）` : `端口 ${port}`;
      });
      await this.step('migrate', () => this.backend.migrate(this.postgres.url()));
      await this.step('service', () => this.startService());
      this.state = 'running';
      return this.url;
    } catch (error) {
      this.state = 'failed';
      await this.stop().catch(() => {});
      throw error;
    }
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
