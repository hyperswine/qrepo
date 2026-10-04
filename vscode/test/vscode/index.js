'use strict';
// Inside VS Code (run by test/run-vscode.js): the extension activates on the
// qrepo workspace, syncs a save, explains a line on hover, shows a revision,
// and settles a conflict by command.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const vscode = require('vscode');

const QR = process.env.QREPO_E2E_QR;
const OTHER = process.env.QREPO_E2E_OTHER;
const SERVER = process.env.QREPO_E2E_SERVER;
const qr = (root, ...a) => execFileSync(QR, ['--root', root, ...a], { encoding: 'utf8' });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(what, ok, ms = 15000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (await ok()) return; await wait(100); }
  throw new Error(`timed out waiting for ${what}`);
}

async function run() {
  const root = vscode.workspace.workspaceFolders[0].uri.fsPath;
  const ext = vscode.extensions.getExtension('jasen.qrepo');
  assert.ok(ext, 'the extension is installed');
  await until('activation', () => ext.isActive);
  const { controllers } = ext.exports;
  const c = controllers.get(root).controller;
  await until('the first sync', () => c.state.kind === 'synced');
  assert.strictEqual(JSON.parse(qr(root, 'config')).device, 'vscode-test', 'the device setting reached qr');
  const log = (m) => console.log(`  ${m}`);

  // an edit saved in the editor reaches the server on its own
  const uri = vscode.Uri.file(path.join(root, 'todo.txt'));
  const doc = await vscode.workspace.openTextDocument(uri);
  await vscode.window.showTextDocument(doc);
  const serverHead = () => fs.readFileSync(path.join(SERVER, '.qrepo/HEAD'), 'utf8').trim();
  const before = serverHead();
  const edit = new vscode.WorkspaceEdit();
  edit.insert(uri, new vscode.Position(doc.lineCount - 1, 0), '- apples\n');
  await vscode.workspace.applyEdit(edit);
  await doc.save();
  await until('the save to sync', () => serverHead() !== before);
  log('a saved edit synced by itself');

  // hover: the line just written, by this device; an old line, by the seed
  await until('a hover', async () => {
    const hs = await vscode.commands.executeCommand('vscode.executeHoverProvider', uri, new vscode.Position(6, 2));
    return hs.some((h) => h.contents.some((m) => String(m.value || m).includes('vscode-test')));
  });
  const old = await vscode.commands.executeCommand('vscode.executeHoverProvider', uri, new vscode.Position(0, 2));
  assert.ok(old.some((h) => h.contents.some((m) => String(m.value || m).includes('**local**'))), 'the first line is the seed\'s');
  log('hover names the device and checkpoint of a line');

  // a revision as a read-only document
  const rev = vscode.Uri.from({ scheme: 'qrepo', path: '/todo.txt', query: new URLSearchParams({ root, rev: before }).toString() });
  const revDoc = await vscode.workspace.openTextDocument(rev);
  assert.ok(!revDoc.getText().includes('apples') && revDoc.getText().startsWith('Groceries'), 'the revision is the old text');
  log('a revision opens read-only');

  // the other device changes the line this one is about to change: conflict
  qr(OTHER, 'pull');
  fs.writeFileSync(path.join(OTHER, 'todo.txt'), fs.readFileSync(path.join(OTHER, 'todo.txt'), 'utf8').replace('- eggs', '- 6 eggs'));
  qr(OTHER, 'sync');
  const e2 = new vscode.WorkspaceEdit();
  const line = doc.getText().split('\n').indexOf('- eggs');
  e2.replace(uri, new vscode.Range(line, 0, line, 6), '- 12 eggs');
  await vscode.workspace.applyEdit(e2);
  await doc.save();
  await until('the conflict', () => c.state.kind === 'conflict');
  assert.deepStrictEqual(c.state.record.files.map((f) => f.path), ['todo.txt']);
  const hs = await vscode.commands.executeCommand('vscode.executeHoverProvider', uri, new vscode.Position(line, 3));
  assert.ok(hs.some((h) => h.contents.some((m) => String(m.value || m).includes("laptop's version"))), 'hover shows the other side');
  log('a conflict is detected, and hover shows the other version');

  await vscode.commands.executeCommand('qrepo.keepBoth');
  await until('the resolution to sync', () => c.state.kind === 'synced');
  const text = fs.readFileSync(uri.fsPath, 'utf8');
  assert.ok(text.includes('- 6 eggs\n- 12 eggs\n'), text);
  qr(OTHER, 'pull');
  assert.strictEqual(fs.readFileSync(path.join(OTHER, 'todo.txt'), 'utf8'), text);
  await until('the editor to show the file', () => vscode.workspace.textDocuments.find((d) => d.uri.fsPath === uri.fsPath).getText() === text);
  log('"keep both" by command settles it on both devices');

  // a deleted file is held, not synced, until allowed
  const extra = vscode.Uri.file(path.join(root, 'extra.txt'));
  fs.writeFileSync(extra.fsPath, 'temporary\n');
  await vscode.commands.executeCommand('qrepo.syncNow');
  await until('extra to sync', () => c.state.kind === 'synced');
  fs.unlinkSync(extra.fsPath);
  const held = serverHead();
  await vscode.commands.executeCommand('qrepo.syncNow');
  await until('the delete to be held', () => c.state.kind === 'held');
  assert.strictEqual(serverHead(), held);
  await vscode.commands.executeCommand('qrepo.syncAnyway');
  await until('the delete to sync', () => c.state.kind === 'synced' && serverHead() !== held);
  log('a delete is held until "sync anyway"');
}

module.exports = { run };
