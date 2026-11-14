'use strict';
/*
 * Chromium's sandbox on Linux needs either user namespaces or a chrome-sandbox helper that root owns
 * with the setuid bit. Ubuntu 24.04 and later allow user namespaces only to programs with an
 * AppArmor profile, which an AppImage, an unpacked folder or a development checkout does not have;
 * Electron then stops with "The SUID sandbox helper binary was found, but is not configured
 * correctly". In that case the program is started with --no-sandbox instead. The window only shows
 * the start-up screen and the local service, and navigation elsewhere is blocked (src/policy.js).
 *
 * Used by scripts/dev.mjs, and by electron-builder.config.cjs for the start script that the Linux
 * packages run in place of the program.
 */
const fs = require('node:fs');
const {spawnSync} = require('node:child_process');

function read(file) {
  try { return fs.readFileSync(file, 'utf8').trim(); } catch { return null; }
}

/** chrome-sandbox owned by root with the setuid bit: the sandbox works without namespaces. */
function helperReady(helper) {
  try {
    const stat = fs.statSync(helper);
    return stat.uid === 0 && (stat.mode & 0o4000) !== 0;
  } catch {
    return false;
  }
}

/** Try what Chromium needs; without unshare, fall back to the kernel switches. */
function namespacesUsable({probe, readFile = read} = {}) {
  const result = (probe || (() => spawnSync('unshare', ['--user', '--map-root-user', 'true'], {stdio: 'ignore', timeout: 5000})))();
  if (!result.error) return result.status === 0;
  return readFile('/proc/sys/kernel/apparmor_restrict_unprivileged_userns') !== '1'
    && readFile('/proc/sys/kernel/unprivileged_userns_clone') !== '0'
    && readFile('/proc/sys/user/max_user_namespaces') !== '0';
}

function needsNoSandbox(helper, {platform = process.platform, ...options} = {}) {
  return platform === 'linux' && !helperReady(helper) && !namespacesUsable(options);
}

/** The start script that takes the program's name in the Linux packages; the program is <program>. */
function launcher(program) {
  return `#!/bin/sh
# Starts ${program} with Chromium's sandbox when this system allows it, and without it otherwise
# (Ubuntu 24.04 and later restrict user namespaces for programs without an AppArmor profile).
here=$(dirname "$(readlink -f "$0")")
program="$here/${program}"
helper="$here/chrome-sandbox"
if [ -u "$helper" ] && [ "$(stat -c %u "$helper" 2>/dev/null)" = 0 ]; then
  exec "$program" "$@"
fi
if command -v unshare >/dev/null 2>&1; then
  if unshare --user --map-root-user true >/dev/null 2>&1; then exec "$program" "$@"; fi
elif [ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null)" != 1 ] \\
  && [ "$(cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null)" != 0 ] \\
  && [ "$(cat /proc/sys/user/max_user_namespaces 2>/dev/null)" != 0 ]; then
  exec "$program" "$@"
fi
exec "$program" --no-sandbox "$@"
`;
}

module.exports = {helperReady, namespacesUsable, needsNoSandbox, launcher};
