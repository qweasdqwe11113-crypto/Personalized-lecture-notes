'use strict';
/* Small decisions the window makes, kept free of Electron so they can be tested. */

function origin(url) {
  try { return new URL(url).origin; } catch { return null; }
}

/** The service page, on the exact local origin the client started. */
function isAppUrl(url, appOrigin) {
  return Boolean(appOrigin) && origin(url) === appOrigin;
}

function isLauncherUrl(url, launcherUrl) {
  return typeof url === 'string' && url.split(/[?#]/)[0] === launcherUrl;
}

/** Links that leave the workspace open in the system browser; anything else is dropped. */
function externalTarget(url) {
  try {
    const target = new URL(url);
    return ['http:', 'https:', 'mailto:'].includes(target.protocol) ? target.href : null;
  } catch {
    return null;
  }
}

/** True when tag (e.g. "v1.2.0") names a newer version than current ("1.1.3"). */
function isNewer(tag, current) {
  const parse = v => String(v || '').replace(/^v/i, '').split(/[.+-]/).slice(0, 3).map(n => Number.parseInt(n, 10) || 0);
  const [a, b] = [parse(tag), parse(current)];
  for (let i = 0; i < 3; i += 1) if (a[i] !== b[i]) return a[i] > b[i];
  return false;
}

const ALLOWED_PERMISSIONS = new Set(['clipboard-sanitized-write', 'fullscreen']);

module.exports = {origin, isAppUrl, isLauncherUrl, externalTarget, isNewer, ALLOWED_PERMISSIONS};
