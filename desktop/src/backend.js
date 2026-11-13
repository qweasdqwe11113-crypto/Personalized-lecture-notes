'use strict';
/* The Python service (demo/init_db.py, then demo/app.py) with its data folder, edition and the
   database address passed through the environment. */
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {EventEmitter} = require('node:events');
const {cleanEnv, run, killTree} = require('./processes');

function serviceEnv({workspace, edition, databaseUrl, port, packaged}) {
  return cleanEnv({
    DATABASE_URL: databaseUrl,
    PORT: String(port),
    ZHIYE_DATA_DIR: workspace,
    ZHIYE_EDITION: edition === 'release' ? 'release' : 'demo',
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
    PYTHONUNBUFFERED: '1',
    // The installed program folder may be read-only; its code is compiled when it is built.
    ...(packaged ? {PYTHONDONTWRITEBYTECODE: '1'} : {}),
  });
}

function get(url, timeout = 3000) {
  return new Promise((resolve, reject) => {
    const request = http.get(url, {timeout}, response => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', chunk => { body += chunk; });
      response.on('end', () => resolve({status: response.statusCode, body}));
    });
    request.on('timeout', () => request.destroy(new Error('timeout')));
    request.on('error', reject);
  });
}

class Backend extends EventEmitter {
  constructor({python, root, workspace, edition, packaged, log}) {
    super();
    Object.assign(this, {python, root, workspace, edition, packaged, log});
    this.child = null;
    this.port = null;
    this.stopping = false;
  }

  settings() {
    return {workspace: this.workspace, edition: this.edition, packaged: this.packaged};
  }

  get url() {
    return this.port ? `http://127.0.0.1:${this.port}` : null;
  }

  check() {
    if (!fs.existsSync(this.python)) throw new Error('找不到随附的 Python 运行环境，安装可能不完整，请重新安装。');
    if (!fs.existsSync(path.join(this.root, 'demo', 'app.py'))) throw new Error('找不到服务程序文件，安装可能不完整，请重新安装。');
    fs.mkdirSync(this.workspace, {recursive: true});
  }

  /** Create or upgrade the tables (and, in the test edition, the demonstration rows). */
  async migrate(databaseUrl) {
    this.check();
    const env = serviceEnv({...this.settings(), databaseUrl, port: 0});
    const {code, output} = await run(this.python, [path.join('demo', 'init_db.py')],
      {cwd: this.root, env, log: this.log, source: 'init_db', timeout: 300000});
    if (code !== 0) throw Object.assign(new Error('数据库结构初始化失败。'), {output});
  }

  async start(databaseUrl, port) {
    this.check();
    this.stopping = false;
    this.port = port;
    const env = serviceEnv({...this.settings(), databaseUrl, port});
    const child = spawn(this.python, [path.join('demo', 'app.py')], {cwd: this.root, env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true});
    this.child = child;
    child.stdout.on('data', chunk => this.log.write('service', chunk));
    child.stderr.on('data', chunk => this.log.write('service', chunk));
    const exited = new Promise(resolve => child.once('exit', (code, signal) => resolve({code, signal})));
    child.once('error', error => this.log.line('client', `服务进程无法启动：${error.message}`));
    exited.then(({code, signal}) => {
      this.log.flush();
      if (this.child === child) this.child = null;
      if (!this.stopping) this.emit('exit', {code, signal});
    });
    const deadline = Date.now() + 90000;
    while (Date.now() < deadline) {
      const outcome = await Promise.race([exited.then(r => ({exited: r})), this.ready().then(ok => ({ok}))]);
      if (outcome.exited) throw Object.assign(new Error('服务启动后立即退出了。'), {output: this.log.tail(30).join('\n')});
      if (outcome.ok) return this.url;
      await new Promise(resolve => setTimeout(resolve, 300));
    }
    await this.stop();
    throw Object.assign(new Error('服务在 90 秒内没有就绪。'), {output: this.log.tail(30).join('\n')});
  }

  async ready() {
    try {
      const {status, body} = await get(`${this.url}/api/health`);
      return status === 200 && JSON.parse(body).database_ok === true;
    } catch {
      return false;
    }
  }

  async stop() {
    this.stopping = true;
    const child = this.child;
    this.child = null;
    if (child) await killTree(child);
  }
}

module.exports = {Backend, serviceEnv, get};
