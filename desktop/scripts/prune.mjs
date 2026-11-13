// Parts of the portable Python that the service never uses. Kept apart from stage.mjs so the rule
// can be checked against both platforms' layouts (test/prune.test.js).
import fs from 'node:fs';
import path from 'node:path';

// Tcl/Tk runtime libraries and the _tkinter extension: libtcl9.0.so, libtcl9tk9.0.so, tcl86t.dll,
// tk86t.dll, _tkinter.pyd. A version number must follow the name, so threading.py never matches.
export const TK_RUNTIME = /^(lib)?(tcl|tk|itcl|thread)\d|^_tkinter/i;

/** Parts of the standard library the service never uses (GUI toolkit, its own tests). */
export function pruneStdlib(root, win = process.platform === 'win32') {
  const lib = win ? path.join(root, 'Lib') : path.join(root, 'lib', fs.readdirSync(path.join(root, 'lib')).find(n => /^python3\.\d+$/.test(n)));
  for (const name of ['test', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip', 'lib2to3', 'pydoc_data']) {
    fs.rmSync(path.join(lib, name), {recursive: true, force: true});
  }
  for (const name of ['tcl', 'share']) fs.rmSync(path.join(root, name), {recursive: true, force: true});
  // Only the folders that hold native libraries. On Windows "lib" is the standard library itself
  // (file names ignore case there), so it is never scanned.
  const native = win ? [path.join(root, 'DLLs')] : [path.join(root, 'lib'), path.join(lib, 'lib-dynload')];
  for (const dir of native) {
    if (!fs.existsSync(dir)) continue;
    for (const name of fs.readdirSync(dir)) {
      if (TK_RUNTIME.test(name)) fs.rmSync(path.join(dir, name), {recursive: true, force: true});
    }
  }
}
