// Installer settings. ZHIYE_FLAVOR=release builds 知页 (empty database); ZHIYE_FLAVOR=demo builds
// 知页测试版 (with the demonstration courses), which installs beside it with its own data. On Linux
// both are built as AppImage, tar.gz and .deb; the .deb packages are named zhiye and zhiye-test and
// install into /opt/知页 and /opt/知页测试版, so the two can be installed together.
const fs = require('node:fs');
const path = require('node:path');
const {launcher} = require('./scripts/linux-sandbox.cjs');

const test = process.env.ZHIYE_FLAVOR === 'demo';
const productName = test ? '知页测试版' : '知页';
const slug = test ? 'zhiye-test' : 'zhiye';

module.exports = {
  appId: test ? 'cn.zhiye.desktop.test' : 'cn.zhiye.desktop',
  productName,
  executableName: slug,
  artifactName: `${slug}-\${version}-\${os}-\${arch}.\${ext}`,
  copyright: '知页项目组',
  directories: {output: `dist/${test ? 'demo' : 'release'}`, buildResources: 'assets'},
  // @embedded-postgres is an optional dependency only so that development (src/paths.js) and
  // scripts/stage.mjs can find PostgreSQL; the packages carry the staged copy in resources/postgres.
  // Without the exclusion electron-builder would add a second, unused copy (about 58 MB).
  files: ['src/**/*', 'assets/icon.png', 'package.json', '!node_modules/@embedded-postgres{,/**/*}'],
  extraMetadata: {productName, zhiye: {edition: test ? 'demo' : 'release'}},
  extraResources: [
    {from: 'build/resources/python', to: 'python'},
    {from: 'build/resources/backend', to: 'backend'},
    {from: 'build/resources/postgres', to: 'postgres'},
  ],
  asar: true,
  electronLanguages: ['zh-CN', 'en-US'],
  linux: {
    target: ['AppImage', 'tar.gz', 'deb'],
    category: 'Education',
    maintainer: '知页项目组',
    icon: 'assets/icon.png',
    synopsis: '个性化讲义、出题与阅卷的教师备课工具',
    executableArgs: [],
    desktop: {entry: {Name: productName, Comment: '个性化讲义、出题与阅卷的教师备课工具', StartupWMClass: slug}},
  },
  deb: {
    packageName: slug,
    // electron-builder's list, plus the two libraries Electron loads that it leaves out. ALSA is named
    // as libasound2t64 first: on Ubuntu 24.04 a bare libasound2 can be met by liboss4-salsa-asound2,
    // which lacks functions Electron needs, and the program then does not start.
    depends: ['libgtk-3-0', 'libnotify4', 'libnss3', 'libxss1', 'libxtst6', 'xdg-utils', 'libatspi2.0-0', 'libuuid1',
      'libsecret-1-0', 'libgbm1', 'libasound2t64 | libasound2'],
    // The interface is Chinese; desktop Ubuntu has these fonts already, minimal installs show boxes
    // without them. Replaces electron-builder's libappindicator3-1, which is only for tray icons
    // (the client has none) and no longer exists on Ubuntu 24.04.
    recommends: ['fonts-noto-cjk'],
    // Makes chrome-sandbox setuid root on every system; see the script.
    afterInstall: 'scripts/deb-after-install.tpl',
  },
  win: {
    target: [{target: 'nsis', arch: ['x64']}],
    icon: 'assets/icon.png',
  },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: productName,
    uninstallDisplayName: productName,
    installerLanguages: ['zh_CN'],
    language: '2052',
    // Teaching data lives in the user's data folder and is kept when the program is removed.
    deleteAppDataOnUninstall: false,
  },
  publish: null,
  // Linux: the program becomes <name>-bin and a start script takes its name, so the AppImage, the
  // tar.gz and the .deb (its /usr/bin link and menu entry) all start through it
  // (scripts/linux-sandbox.cjs explains why).
  afterPack(context) {
    if (context.electronPlatformName !== 'linux') return;
    const program = path.join(context.appOutDir, slug);
    fs.renameSync(program, `${program}-bin`);
    fs.writeFileSync(program, launcher(`${slug}-bin`), {mode: 0o755});
  },
};
