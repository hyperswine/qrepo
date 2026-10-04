#!/usr/bin/env node
'use strict';
// Runs test/vscode/index.js inside a real VS Code: an isolated user-data and
// extensions directory (nothing of the person's VS Code is read or changed),
// this folder as the extension under development, and a qrepo clone, synced to
// a local server, as the workspace.  A window opens for a few seconds.
//
//   node test/run-vscode.js            (VSCODE=/path/to/Code to choose one)
const fs = require('fs');
const os = require('os');
const path = require('path');
const net = require('net');
const { spawn, execFileSync } = require('child_process');

const QR = process.env.QR || path.resolve(__dirname, '../../qr');
const CODE = process.env.VSCODE || '/Applications/Visual Studio Code.app/Contents/MacOS/Code';
const qr = (root, ...a) => execFileSync(QR, ['--root', root, ...a], { encoding: 'utf8' });

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'qrepo-vscode-e2e-'));
  const [srv, ws, other, user, exts] = ['srv', 'workspace', 'other', 'user', 'extensions'].map((n) => { const p = path.join(dir, n); fs.mkdirSync(p); return p; });
  fs.writeFileSync(path.join(srv, 'todo.txt'), 'Groceries\n- milk\n- eggs\n- bread\n- rice\n- tea\n');
  qr(srv, 'init'); qr(srv, 'commit', 'seed');
  const port = await new Promise((r) => { const s = net.createServer().listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => r(p)); }); });
  const server = spawn(QR, ['--root', srv, 'serve', String(port)], { stdio: 'ignore' });
  for (let i = 0; i < 100; i++) {
    const up = await new Promise((r) => { const c = net.connect(port, '127.0.0.1', () => { c.destroy(); r(true); }).on('error', () => r(false)); });
    if (up) break;
    await new Promise((r) => setTimeout(r, 50));
  }
  qr(ws, 'clone', String(port)); qr(other, 'clone', String(port));
  qr(other, 'config', 'device', 'laptop');
  fs.mkdirSync(path.join(user, 'User'), { recursive: true });
  fs.writeFileSync(path.join(user, 'User', 'settings.json'), JSON.stringify({
    'qrepo.path': QR, 'qrepo.device': 'vscode-test', 'qrepo.idleDelay': 1, 'qrepo.syncInterval': 5,
    'security.workspace.trust.enabled': false, 'workbench.startupEditor': 'none', 'update.mode': 'none',
    'extensions.autoUpdate': false, 'telemetry.telemetryLevel': 'off',
  }));
  const code = await new Promise((resolve) => {
    const child = spawn(CODE, [
      ws, '--new-window', '--disable-workspace-trust', '--skip-welcome', '--skip-release-notes', '--disable-gpu',
      `--user-data-dir=${user}`, `--extensions-dir=${exts}`,
      `--extensionDevelopmentPath=${path.resolve(__dirname, '..')}`,
      `--extensionTestsPath=${path.resolve(__dirname, 'vscode', 'index.js')}`,
    ], { stdio: 'inherit', env: { ...process.env, QREPO_E2E_QR: QR, QREPO_E2E_OTHER: other, QREPO_E2E_SERVER: srv, ELECTRON_RUN_AS_NODE: undefined } });
    child.on('exit', (c) => resolve(c));
  });
  server.kill();
  fs.rmSync(dir, { recursive: true, force: true });
  process.exit(code);
})();
