'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const load = () => import('../scripts/prune.mjs');

function tree(root, files) {
  for (const file of files) {
    const full = path.join(root, ...file.split('/'));
    fs.mkdirSync(path.dirname(full), {recursive: true});
    if (!file.endsWith('/')) fs.writeFileSync(full, '');
  }
}
const exists = (root, file) => fs.existsSync(path.join(root, ...file.split('/')));

test('the Tcl/Tk rule matches the runtime libraries and no standard-library module', async () => {
  const {TK_RUNTIME} = await load();
  for (const name of ['libtcl9.0.so', 'libtcl9tk9.0.so', 'tcl86t.dll', 'tk86t.dll', '_tkinter.pyd',
    '_tkinter.cpython-312-x86_64-linux-gnu.so', 'itcl4.2.4', 'thread2.8.9']) {
    assert.match(name, TK_RUNTIME, name);
  }
  for (const name of ['threading.py', '_threading_local.py', 'tkinter', 'tomllib', 'token.py', 'tokenize.py', 'trace.py',
    'traceback.py', 'tempfile.py', 'textwrap.py', 'this.py', 'timeit.py', 'tty.py', 'turtle.py', 'typing.py',
    'libpython3.12.so', 'python312.dll', 'sqlite3.dll', '_socket.pyd']) {
    assert.doesNotMatch(name, TK_RUNTIME, name);
  }
});

test('Windows layout: the GUI toolkit goes, the standard library stays', async () => {
  const {pruneStdlib} = await load();
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'zhiye-prune-'));
  try {
    tree(root, ['python.exe', 'Lib/threading.py', 'Lib/logging/__init__.py', 'Lib/tkinter/__init__.py', 'Lib/test/x.py',
      'DLLs/tcl86t.dll', 'DLLs/tk86t.dll', 'DLLs/_tkinter.pyd', 'DLLs/_socket.pyd', 'DLLs/sqlite3.dll', 'tcl/tcl8.6/init.tcl']);
    pruneStdlib(root, true);
    for (const kept of ['python.exe', 'Lib/threading.py', 'Lib/logging/__init__.py', 'DLLs/_socket.pyd', 'DLLs/sqlite3.dll']) {
      assert.ok(exists(root, kept), `${kept} kept`);
    }
    for (const gone of ['Lib/tkinter', 'Lib/test', 'DLLs/tcl86t.dll', 'DLLs/tk86t.dll', 'DLLs/_tkinter.pyd', 'tcl']) {
      assert.ok(!exists(root, gone), `${gone} removed`);
    }
  } finally { fs.rmSync(root, {recursive: true, force: true}); }
});

test('Linux layout: the GUI toolkit goes, the standard library stays', async () => {
  const {pruneStdlib} = await load();
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'zhiye-prune-'));
  try {
    const lib = 'lib/python3.12';
    tree(root, ['bin/python3', 'lib/libpython3.12.so.1.0', 'lib/libtcl9.0.so', 'lib/libtcl9tk9.0.so', `${lib}/threading.py`,
      `${lib}/tkinter/__init__.py`, `${lib}/lib-dynload/_tkinter.cpython-312-x86_64-linux-gnu.so`,
      `${lib}/lib-dynload/_socket.cpython-312-x86_64-linux-gnu.so`, 'share/man/man1/python3.1']);
    pruneStdlib(root, false);
    for (const kept of ['bin/python3', 'lib/libpython3.12.so.1.0', `${lib}/threading.py`, `${lib}/lib-dynload/_socket.cpython-312-x86_64-linux-gnu.so`]) {
      assert.ok(exists(root, kept), `${kept} kept`);
    }
    for (const gone of ['lib/libtcl9.0.so', 'lib/libtcl9tk9.0.so', `${lib}/tkinter`, `${lib}/lib-dynload/_tkinter.cpython-312-x86_64-linux-gnu.so`, 'share']) {
      assert.ok(!exists(root, gone), `${gone} removed`);
    }
  } finally { fs.rmSync(root, {recursive: true, force: true}); }
});
