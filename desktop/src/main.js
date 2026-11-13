'use strict';
/* 知页 desktop client: starts the bundled database and service, shows the start-up screen while
   they come up, then opens the workspace page in the same window. */
const {app, BrowserWindow, Menu, dialog, ipcMain, net, session, shell} = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const config = require('./config');
const paths = require('./paths');
const backup = require('./backup');
const upgrade = require('./upgrade');
const policy = require('./policy');
const {Runtime, STEPS} = require('./runtime');
const pkg = require('../package.json');

const packaged = app.isPackaged;
const edition = paths.edition(packaged ? pkg.zhiye && pkg.zhiye.edition : process.env.ZHIYE_DESKTOP_EDITION);
const editionLabel = paths.EDITIONS[edition].label;
const productName = edition === 'demo' ? '知页测试版' : '知页';
// A fixed port keeps the page's origin, and the preferences it remembers, the same between runs.
const PREFERRED_PORT = (edition === 'demo' ? 38661 : 38660) + (packaged ? 0 : 10);
const REPOSITORY = 'qweasdqwe11113-crypto/Personalized-lecture-notes';
const LAUNCHER = path.join(__dirname, 'launcher', 'index.html');
const LAUNCHER_URL = pathToFileURL(LAUNCHER).href;
const ICON = path.join(__dirname, '..', 'assets', 'icon.png');

// Run from source, each edition keeps its own settings instead of sharing the installed 知页's.
if (!packaged) app.setPath('userData', path.join(app.getPath('appData'), `${productName}-Dev`));

let settings = config.DEFAULTS;
let runtime = null;
let win = null;
let state = null;
let quitting = false;
let stopped = false;
let busy = false;
let noSandbox = false;

const configFile = () => path.join(app.getPath('userData'), 'desktop.json');
const dataDir = () => settings.dataDir || paths.defaultDataDir(paths.dataFolderName(edition, !packaged));
const appOrigin = () => (runtime && runtime.url ? policy.origin(runtime.url) : null);

function resources() {
  if (process.env.ZHIYE_DESKTOP_RESOURCES) return paths.stagedResources(path.resolve(process.env.ZHIYE_DESKTOP_RESOURCES));
  return packaged ? paths.stagedResources(process.resourcesPath) : paths.developmentResources(path.join(__dirname, '..'));
}

// ---------------------------------------------------------------- start-up screen state

function info() {
  return {version: app.getVersion(), edition, editionLabel, productName, dataDir: dataDir(), platform: process.platform};
}

function freshState(phase, lead) {
  return {phase, lead, steps: STEPS.map(s => ({...s, state: 'waiting', detail: ''})), error: null, info: info()};
}

function publish(patch) {
  state = {...state, ...patch, info: info()};
  if (win && !win.isDestroyed() && policy.isLauncherUrl(win.webContents.getURL(), LAUNCHER_URL)) {
    win.webContents.send('launcher:state', state);
  }
}

async function showLauncher(next) {
  state = {...next, info: info()};
  if (!win || win.isDestroyed()) return;
  if (policy.isLauncherUrl(win.webContents.getURL(), LAUNCHER_URL)) publish({});
  else await win.loadFile(LAUNCHER).catch(() => {});
}

const HINTS = {
  prepare: '数据目录无法写入。可以更换到有写入权限的文件夹。',
  database: '常见原因：数据目录所在磁盘已满、被安全软件拦截，或数据目录路径中含有系统无法识别的字符。可以重试，或更换数据目录。',
  migrate: '数据库已启动，但数据表没有准备好。请查看日志；若是升级后出现，升级前的数据在数据目录的「升级前备份-…」文件夹中，可以用「服务 → 从备份恢复」找回。请保留数据目录并反馈问题。',
  service: '教学服务没有启动成功。请查看日志中的最后几行。',
};

function failure(error, title = '启动没有完成') {
  return {
    title: error.title || title,
    message: error.message,
    hint: error.hint || HINTS[error.step] || '',
    step: error.step || null,
    log: runtime ? runtime.log.tail(40) : [],
  };
}

// ---------------------------------------------------------------- runtime lifecycle

function makeRuntime() {
  const next = new Runtime({resources: resources(), dataDir: dataDir(), edition, packaged, preferredPort: PREFERRED_PORT});
  if (noSandbox) next.log.line('client', '本系统限制了 Chromium 沙箱（Ubuntu 24.04 起的 AppArmor 规则），本次不使用沙箱运行。');
  next.on('step', step => {
    if (!state || !state.steps) return;
    publish({steps: state.steps.map(s => (s.id === step.id ? {...s, state: step.state, detail: step.detail || ''} : s))});
  });
  // A long part of a step, such as the backup before an upgrade, gets the line under the title.
  next.on('progress', text => publish({lead: text || '正在启动本机服务…'}));
  next.on('service-exit', () => {
    if (quitting || busy) return;
    showLauncher({...freshState('failed', '教学服务意外停止'),
      error: {title: '教学服务意外停止', message: '服务进程已经退出，数据库仍在运行，数据不受影响。', hint: '点击「重新启动服务」继续；如果反复出现，请查看日志。', step: 'service', retryLabel: '重新启动服务', log: next.log.tail(40)}});
  });
  return next;
}

async function boot() {
  busy = true;
  await showLauncher(freshState('starting', '正在启动本机服务…'));
  if (runtime) await runtime.stop().catch(() => {});
  runtime = makeRuntime();
  try {
    const url = await runtime.start();
    busy = false;
    await openWorkspace(url);
  } catch (error) {
    busy = false;
    await showLauncher({...state, phase: 'failed', lead: '启动没有完成', error: failure(error)});
  }
}

async function openWorkspace(url) {
  if (!win || win.isDestroyed()) return;
  publish({phase: 'opening', lead: '正在打开工作空间…'});
  await win.loadURL(url + '/').catch(error => runtime && runtime.log.line('client', `页面加载失败：${error.message}`));
}

/** Restart only the Python service (model settings take effect); the database keeps running. */
async function restartService() {
  if (busy) return;
  if (!runtime || runtime.postgres.port === null) return boot();
  busy = true;
  await showLauncher({...freshState('restarting', '正在重启教学服务…'),
    steps: freshState().steps.map(s => (s.id === 'service' ? s : {...s, state: 'done'}))});
  try {
    const url = await runtime.restartService();
    busy = false;
    await openWorkspace(url);
  } catch (error) {
    busy = false;
    await showLauncher({...state, phase: 'failed', lead: '服务没有重启成功', error: failure(error, '服务没有重启成功')});
  }
}

async function requestQuit() {
  if (quitting) return;
  quitting = true;
  saveBounds();
  if (runtime) {
    await showLauncher({...freshState('stopping', '正在安全关闭数据库与服务…'), steps: []});
    await runtime.stop().catch(() => {});
  }
  stopped = true;
  app.quit();
}

// ---------------------------------------------------------------- maintenance: data folder, backups

async function withServiceStopped(lead, work) {
  if (busy) return;
  busy = true;
  await showLauncher({...freshState('maintenance', lead), steps: []});
  try {
    if (runtime) await runtime.stop();
    return await work();
  } finally {
    busy = false;
    await boot();
  }
}

async function chooseDataDir() {
  const {canceled, filePaths} = await dialog.showOpenDialog(win, {
    title: '选择数据目录', defaultPath: path.dirname(dataDir()), buttonLabel: '使用此文件夹',
    properties: ['openDirectory', 'createDirectory'],
  });
  if (canceled || !filePaths[0]) return false;
  const chosen = path.join(filePaths[0], paths.dataFolderName(edition, !packaged));
  const {response} = await dialog.showMessageBox(win, {
    type: 'question', buttons: ['使用新目录', '取消'], defaultId: 0, cancelId: 1, title: '更换数据目录',
    message: `之后的数据将保存在：\n${chosen}`,
    detail: '原数据目录中的内容不会被删除或移动；如需沿用原有数据，请先备份，再在新目录中「从备份恢复」。',
  });
  if (response !== 0) return false;
  settings = {...settings, dataDir: chosen};
  config.save(configFile(), settings);
  await boot();
  return true;
}

async function backupData() {
  const {canceled, filePaths} = await dialog.showOpenDialog(win, {
    title: '选择备份保存位置', defaultPath: app.getPath('documents'), buttonLabel: '保存到此处',
    properties: ['openDirectory', 'createDirectory'],
  });
  if (canceled || !filePaths[0]) return;
  const {response} = await dialog.showMessageBox(win, {
    type: 'question', buttons: ['开始备份', '取消'], defaultId: 0, cancelId: 1, title: '备份数据',
    message: '备份期间会暂停服务，通常不超过一分钟。',
    detail: '备份包含数据库、上传的资料、阅卷记录和模型设置（含已保存的 API 密钥），请妥善保管备份文件夹。正在进行的生成任务会中断。',
  });
  if (response !== 0) return;
  let folder = null, failed = null;
  await withServiceStopped('正在备份数据…', async () => {
    // The version the data belongs to: still the older one while an upgrade has not finished.
    const version = upgrade.dataVersion(dataDir()) || app.getVersion();
    try { folder = backup.createBackup(dataDir(), filePaths[0], {version, edition}); } catch (error) { failed = error; }
  });
  if (failed) return dialog.showErrorBox('备份失败', failed.message);
  const done = await dialog.showMessageBox(win, {type: 'info', buttons: ['打开备份文件夹', '完成'], defaultId: 1, title: '备份完成', message: '备份已保存。', detail: folder});
  if (done.response === 0) shell.showItemInFolder(folder);
}

async function restoreData() {
  // Backups made before an upgrade sit in the data folder; start there when there are any.
  const nearby = upgrade.listUpgradeBackups(dataDir()).length > 0;
  const {canceled, filePaths} = await dialog.showOpenDialog(win, {
    title: '选择要恢复的备份文件夹（知页备份-… 或 升级前备份-…）', defaultPath: nearby ? dataDir() : app.getPath('documents'),
    buttonLabel: '恢复此备份', properties: ['openDirectory'],
  });
  if (canceled || !filePaths[0]) return;
  let manifest;
  try {
    manifest = backup.checkBackup(filePaths[0], dataDir(), edition);
    upgrade.checkRestore(manifest, pkg.version);
  } catch (error) { return dialog.showErrorBox('无法恢复', error.message); }
  const {response} = await dialog.showMessageBox(win, {
    type: 'warning', buttons: ['恢复', '取消'], defaultId: 1, cancelId: 1, title: '从备份恢复',
    message: `用 ${new Date(manifest.created_at).toLocaleString('zh-CN')} 的备份替换当前数据？`,
    detail: '当前数据不会删除，会移到数据目录中的「恢复前-时间」文件夹。恢复期间服务暂停。',
  });
  if (response !== 0) return;
  let aside = null, failed = null;
  await withServiceStopped('正在从备份恢复…', async () => {
    try { aside = backup.restoreBackup(filePaths[0], dataDir()); } catch (error) { failed = error; }
    if (failed) return;
    try { upgrade.recordRestore(dataDir(), {version: pkg.version, edition, from: filePaths[0]}); } catch (error) {
      if (runtime) runtime.log.line('client', `恢复后记录客户端版本失败：${error.message}`);
    }
  });
  if (failed) return dialog.showErrorBox('恢复失败', failed.message);
  dialog.showMessageBox(win, {type: 'info', title: '恢复完成', message: '已从备份恢复。', detail: `原数据保存在：${aside}`});
}

// ---------------------------------------------------------------- help: about, updates

function showAbout() {
  dialog.showMessageBox(win, {
    type: 'info', title: `关于${productName}`, message: `${productName} ${app.getVersion()}（${editionLabel}）`,
    detail: [`个性化讲义、出题与阅卷的教师备课工具。`, '', `数据目录：${dataDir()}`,
      `Electron ${process.versions.electron} · Chromium ${process.versions.chrome} · Node ${process.versions.node}`].join('\n'),
  });
}

async function checkUpdates() {
  try {
    const response = await net.fetch(`https://api.github.com/repos/${REPOSITORY}/releases/latest`,
      {headers: {Accept: 'application/vnd.github+json', 'User-Agent': 'zhiye-desktop'}});
    if (response.status === 404) return dialog.showMessageBox(win, {type: 'info', title: '检查更新', message: '暂时还没有发布的版本。'});
    if (!response.ok) throw new Error(`GitHub 返回 ${response.status}`);
    const release = await response.json();
    if (!policy.isNewer(release.tag_name, app.getVersion())) {
      return dialog.showMessageBox(win, {type: 'info', title: '检查更新', message: `已是最新版本（${app.getVersion()}）。`});
    }
    const {response: choice} = await dialog.showMessageBox(win, {
      type: 'info', buttons: ['前往下载', '以后再说'], defaultId: 0, cancelId: 1, title: '发现新版本',
      message: `新版本 ${release.tag_name} 可以下载。`, detail: '安装新版本会保留现有数据。',
    });
    const page = policy.externalTarget(release.html_url);
    if (choice === 0 && page) shell.openExternal(page);
  } catch (error) {
    dialog.showMessageBox(win, {type: 'warning', title: '检查更新', message: '暂时无法检查更新。', detail: error.message});
  }
}

// ---------------------------------------------------------------- window and menu

function saveBounds() {
  if (!win || win.isDestroyed()) return;
  settings = {...settings, bounds: {...win.getNormalBounds(), maximized: win.isMaximized()}};
  try { config.save(configFile(), settings); } catch { /* not essential */ }
}

function createWindow() {
  const bounds = settings.bounds || {width: 1360, height: 880};
  win = new BrowserWindow({
    ...bounds, minWidth: 960, minHeight: 640, show: false, title: productName, icon: ICON,
    backgroundColor: '#f5f7fa', autoHideMenuBar: false,
    webPreferences: {preload: path.join(__dirname, 'preload.js'), contextIsolation: true, sandbox: true,
      nodeIntegration: false, spellcheck: false, devTools: !packaged || edition === 'demo'},
  });
  if (settings.bounds && settings.bounds.maximized) win.maximize();
  win.once('ready-to-show', () => win.show());
  win.on('close', event => { if (!stopped) { event.preventDefault(); requestQuit(); } });
  win.on('page-title-updated', (event, title) => {
    event.preventDefault();
    win.setTitle(edition === 'demo' && !title.includes('测试版') ? `${title}（测试版）` : title);
  });
  const contents = win.webContents;
  contents.on('will-navigate', (event, url) => {
    if (policy.isAppUrl(url, appOrigin()) || policy.isLauncherUrl(url, LAUNCHER_URL)) return;
    event.preventDefault();
    const target = policy.externalTarget(url);
    if (target) shell.openExternal(target);
  });
  contents.setWindowOpenHandler(({url}) => {
    if (policy.isAppUrl(url, appOrigin())) {
      return {action: 'allow', overrideBrowserWindowOptions: {autoHideMenuBar: true, icon: ICON, backgroundColor: '#ffffff'}};
    }
    const target = policy.externalTarget(url);
    if (target) shell.openExternal(target);
    return {action: 'deny'};
  });
  contents.on('render-process-gone', (_event, details) => {
    if (quitting) return;
    if (runtime) runtime.log.line('client', `页面进程结束（${details.reason}），重新载入。`);
    contents.reload();
  });
}

/** 帮助 → 新手引导: the workspace page runs the tour; the start-up screen has none. */
function showTour() {
  if (win && !win.isDestroyed() && policy.isAppUrl(win.webContents.getURL(), appOrigin())) {
    win.webContents.send('desktop:show-tour');
  }
}

function buildMenu() {
  const reload = () => { if (win && runtime && runtime.url && !busy) win.loadURL(runtime.url + '/'); };
  const template = [
    {label: productName, submenu: [
      {label: `关于${productName}`, click: showAbout},
      {label: '检查更新…', click: checkUpdates},
      {type: 'separator'},
      {label: '退出', accelerator: 'CmdOrCtrl+Q', click: requestQuit},
    ]},
    {label: '编辑', submenu: [
      {label: '撤销', role: 'undo'}, {label: '重做', role: 'redo'}, {type: 'separator'},
      {label: '剪切', role: 'cut'}, {label: '复制', role: 'copy'}, {label: '粘贴', role: 'paste'}, {label: '全选', role: 'selectAll'},
    ]},
    {label: '视图', submenu: [
      {label: '重新载入', accelerator: 'CmdOrCtrl+R', click: reload},
      {type: 'separator'},
      {label: '实际大小', role: 'resetZoom'}, {label: '放大', role: 'zoomIn'}, {label: '缩小', role: 'zoomOut'},
      {type: 'separator'},
      {label: '全屏', role: 'togglefullscreen'},
      ...(!packaged || edition === 'demo' ? [{label: '开发者工具', role: 'toggleDevTools'}] : []),
    ]},
    {label: '服务', submenu: [
      {label: '重启服务', click: restartService},
      {label: '在浏览器中打开', click: () => runtime && runtime.url && shell.openExternal(runtime.url + '/')},
      {type: 'separator'},
      {label: '备份数据…', click: backupData},
      {label: '从备份恢复…', click: restoreData},
      {type: 'separator'},
      {label: '打开数据目录', click: () => shell.openPath(dataDir())},
      {label: '查看日志', click: () => shell.openPath(path.join(dataDir(), 'logs'))},
      {label: '更换数据目录…', click: chooseDataDir},
    ]},
    {label: '帮助', submenu: [
      {label: '使用说明', click: () => shell.openExternal(`https://github.com/${REPOSITORY}/blob/main/docs/user-guide.md`)},
      {label: '新手引导', click: showTour},
      {label: '问题反馈', click: () => shell.openExternal(`https://github.com/${REPOSITORY}/issues`)},
      {type: 'separator'},
      {label: `关于${productName}`, click: showAbout},
    ]},
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ---------------------------------------------------------------- IPC from the two kinds of page

function from(event, kinds) {
  const url = event.senderFrame ? event.senderFrame.url : '';
  return (kinds.includes('launcher') && policy.isLauncherUrl(url, LAUNCHER_URL)) ||
    (kinds.includes('app') && policy.isAppUrl(url, appOrigin()));
}

function handle(channel, kinds, work) {
  ipcMain.handle(channel, (event, ...args) => {
    if (!from(event, kinds)) throw new Error('不允许的调用来源。');
    return work(...args);
  });
}

function registerIpc() {
  handle('desktop:info', ['launcher', 'app'], info);
  handle('desktop:open-logs', ['launcher', 'app'], () => shell.openPath(path.join(dataDir(), 'logs')));
  handle('desktop:open-data', ['launcher', 'app'], () => shell.openPath(dataDir()));
  handle('desktop:restart-service', ['app'], () => restartService());
  handle('launcher:state', ['launcher'], () => state);
  handle('launcher:retry', ['launcher'], () => (state && state.error && state.error.step === 'service' && runtime && runtime.postgres.port ? restartService() : boot()));
  handle('launcher:choose-data', ['launcher'], () => chooseDataDir());
}

function hardenSession() {
  const ses = session.defaultSession;
  ses.setPermissionRequestHandler((_contents, permission, callback) => callback(policy.ALLOWED_PERMISSIONS.has(permission)));
  ses.setPermissionCheckHandler((_contents, permission) => policy.ALLOWED_PERMISSIONS.has(permission));
  ses.on('will-download', (_event, item) => {
    item.setSaveDialogOptions({defaultPath: path.join(app.getPath('downloads'), item.getFilename())});
  });
}

/** After an upgrade, drop the page's cached copies (the service worker cache); keep its preferences. */
async function forgetOldPages() {
  if (settings.lastVersion === app.getVersion()) return;
  await session.defaultSession.clearStorageData({storages: ['serviceworkers', 'cachestorage']}).catch(() => {});
  settings = {...settings, lastVersion: app.getVersion()};
  try { config.save(configFile(), settings); } catch { /* not essential */ }
}

// ---------------------------------------------------------------- start

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.focus();
  });
  app.on('before-quit', event => {
    if (stopped) return;
    event.preventDefault();
    requestQuit();
  });
  app.on('window-all-closed', () => app.quit());
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => requestQuit());

  app.whenReady().then(async () => {
    app.setAppUserModelId(edition === 'demo' ? 'cn.zhiye.desktop.test' : 'cn.zhiye.desktop');
    settings = config.load(configFile());
    fs.mkdirSync(path.dirname(configFile()), {recursive: true});
    hardenSession();
    registerIpc();
    buildMenu();
    await forgetOldPages();
    // Set by the Linux start script or scripts/dev.mjs where the system blocks Chromium's sandbox.
    noSandbox = app.commandLine.hasSwitch('no-sandbox');
    state = freshState('starting', '正在启动本机服务…');
    createWindow();
    await win.loadFile(LAUNCHER);
    await boot();
  });
}
