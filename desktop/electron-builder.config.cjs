// Installer settings. ZHIYE_FLAVOR=release builds 知页 (empty database); ZHIYE_FLAVOR=demo builds
// 知页测试版 (with the demonstration courses), which installs beside it with its own data.
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
  files: ['src/**/*', 'assets/icon.png', 'package.json'],
  extraMetadata: {productName, zhiye: {edition: test ? 'demo' : 'release'}},
  extraResources: [
    {from: 'build/resources/python', to: 'python'},
    {from: 'build/resources/backend', to: 'backend'},
    {from: 'build/resources/postgres', to: 'postgres'},
  ],
  asar: true,
  electronLanguages: ['zh-CN', 'en-US'],
  linux: {
    target: ['AppImage', 'tar.gz'],
    category: 'Education',
    icon: 'assets/icon.png',
    synopsis: '个性化讲义、出题与阅卷的教师备课工具',
    executableArgs: [],
    desktop: {entry: {Name: productName, Comment: '个性化讲义、出题与阅卷的教师备课工具', StartupWMClass: slug}},
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
  // tar.gz and their menu entries all start through it (scripts/linux-sandbox.cjs explains why).
  afterPack(context) {
    if (context.electronPlatformName !== 'linux') return;
    const program = path.join(context.appOutDir, slug);
    fs.renameSync(program, `${program}-bin`);
    fs.writeFileSync(program, launcher(`${slug}-bin`), {mode: 0o755});
  },
};
