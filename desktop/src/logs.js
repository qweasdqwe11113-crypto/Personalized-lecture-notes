'use strict';
/* One log for the client, PostgreSQL helper output and the Python service: appended to a file
   in the data folder (rotated at a size limit) and kept in memory for the start-up screen. */
const fs = require('node:fs');
const path = require('node:path');

// Terminal colours, and the service's access lines for requests that went fine: neither helps
// anyone reading the log afterwards. Failed requests and every other line are kept.
const COLOURS = /\x1b\[[0-9;]*[A-Za-z]/g;
const ROUTINE = /"(GET|HEAD) [^"]*" (200|204|206|304) -?\s*$/;

class LogSink {
  constructor(file, {limit = 400, maxBytes = 5 * 1024 * 1024} = {}) {
    this.file = file;
    this.limit = limit;
    this.maxBytes = maxBytes;
    this.size = 0;
    this.lines = [];
    this.partial = {};
    if (file) {
      fs.mkdirSync(path.dirname(file), {recursive: true});
      try { this.size = fs.statSync(file).size; } catch { /* no previous log */ }
      this.rotate();
    }
  }

  /** Keep one previous file once the log passes the size limit, also during a long session. */
  rotate() {
    if (this.size <= this.maxBytes) return;
    try { fs.renameSync(this.file, `${this.file}.1`); } catch { /* nothing to move */ }
    this.size = 0;
  }

  stamp() {
    const d = new Date(), p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }

  /** Text may arrive in arbitrary chunks; only complete lines are recorded. */
  write(source, chunk) {
    const text = (this.partial[source] || '') + String(chunk).replace(/\r\n?/g, '\n');
    const parts = text.split('\n');
    this.partial[source] = parts.pop();
    for (const raw of parts) {
      const line = raw.replace(COLOURS, '');
      if (!ROUTINE.test(line)) this.line(source, line);
    }
  }

  line(source, text) {
    const entry = `${this.stamp()} [${source}] ${text}`;
    this.lines.push(entry);
    if (this.lines.length > this.limit) this.lines.splice(0, this.lines.length - this.limit);
    if (this.file) {
      try {
        fs.appendFileSync(this.file, entry + '\n');
        this.size += Buffer.byteLength(entry) + 1;
        this.rotate();
      } catch { /* logging must never stop the program */ }
    }
  }

  flush() {
    const rest = this.partial;
    this.partial = {};
    for (const [source, text] of Object.entries(rest)) if (text) this.write(source, text + '\n');
  }

  tail(count = 60) {
    return this.lines.slice(-count);
  }
}

module.exports = {LogSink};
