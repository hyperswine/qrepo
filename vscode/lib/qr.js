'use strict';
// Running qr.  Every call is `qr --root ROOT ...`; --json answers are parsed.
// A command that fails answers its exit status and what it printed, never
// throws: the controller decides what a failure means.
const { execFile } = require('child_process');

function run(qrPath, root, args, stdin) {
  return new Promise((resolve) => {
    const child = execFile(qrPath, ['--root', root, ...args], { maxBuffer: 64 << 20 }, (err, stdout, stderr) => {
      const code = err ? (typeof err.code === 'number' ? err.code : -1) : 0;
      resolve({ code, stdout: String(stdout), stderr: String(stderr || (err && err.code === 'ENOENT' ? `qr not found at ${qrPath}` : '')) });
    });
    if (stdin !== undefined) child.stdin.end(stdin);
  });
}

function parse(out) {
  try { return JSON.parse(out.stdout); } catch { return undefined; }
}

class Qr {
  constructor(qrPath, root) { this.qrPath = qrPath; this.root = root; }
  run(...args) { return run(this.qrPath, this.root, args); }
  async json(...args) {
    const out = await this.run(...args, '--json');
    return { code: out.code, value: parse(out), error: (out.stderr || out.stdout).trim() };
  }
  status() { return this.json('status'); }
  sync() { return this.json('sync'); }
  conflicts() { return this.json('conflicts'); }
  blame(path) { return this.json('blame', path); }
  resolve(path, pick) { return this.run('resolve', path, pick); }
  commitMerge(message) { return this.run('commit-merge', message); }
  show(rev, path) { return this.run('show', rev, path); }
  async config() { const out = await this.run('config'); return parse(out) || {}; }
  setConfig(key, value) { return this.run('config', key, value); }
}

module.exports = { Qr, run };
