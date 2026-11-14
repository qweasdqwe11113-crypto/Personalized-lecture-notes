'use strict';
/* The client's own small settings file (chosen data folder, window size). Missing or damaged
   files fall back to defaults; writes go through a temporary file so a crash cannot leave half
   a file behind. */
const fs = require('node:fs');
const path = require('node:path');

const DEFAULTS = Object.freeze({dataDir: null, bounds: null, lastVersion: null});

function load(file) {
  try {
    const value = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {...DEFAULTS};
    return {
      dataDir: typeof value.dataDir === 'string' && value.dataDir ? value.dataDir : null,
      bounds: validBounds(value.bounds) ? value.bounds : null,
      lastVersion: typeof value.lastVersion === 'string' ? value.lastVersion : null,
    };
  } catch {
    return {...DEFAULTS};
  }
}

function validBounds(b) {
  return Boolean(b) && ['x', 'y', 'width', 'height'].every(k => Number.isFinite(b[k])) && b.width >= 400 && b.height >= 300;
}

function save(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  const temp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temp, JSON.stringify({...DEFAULTS, ...value}, null, 2));
  fs.renameSync(temp, file);
}

module.exports = {DEFAULTS, load, save, validBounds};
