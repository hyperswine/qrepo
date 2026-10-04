'use strict';
// The sync loop against the real qr and a local server; the UI is a recorder.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const net = require('net');
const { spawn, execFileSync } = require('child_process');
const { Qr } = require('../lib/qr');
const { Controller } = require('../lib/controller');

const QR = process.env.QR || path.resolve(__dirname, '../../qr');
const qr = (root, ...a) => execFileSync(QR, ['--root', root, ...a], { encoding: 'utf8' });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function recorder() {
  const ui = { states: [], held: [], conflicts: [], errors: [], answer: {} };
  ui.setState = (root, s) => ui.states.push(s.kind);
  ui.announceHeld = (root, held) => { ui.held.push(held); return ui.answer.held; };
  ui.reviewHeld = async () => {};
  ui.announceConflict = (root, record, lines, picks) => { ui.conflicts.push({ record, lines, picks }); return ui.answer.conflict; };
  ui.compare = async () => { ui.compared = true; };
  ui.error = (root, m) => ui.errors.push(m);
  return ui;
}
async function port() {
  return new Promise((r) => { const s = net.createServer().listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => r(p)); }); });
}

test('the sync loop: ordinary edits, held changes, a conflict settled', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'qrepo-vscode-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const [srv, a, b] = ['srv', 'a', 'b'].map((n) => { const p = path.join(dir, n); fs.mkdirSync(p); return p; });
  fs.writeFileSync(path.join(srv, 'todo.txt'), 'Groceries\n- milk\n- eggs\n- bread\n- rice\n- tea\n');
  fs.writeFileSync(path.join(srv, 'journal.txt'), Array.from({ length: 30 }, (_, i) => `entry ${i}\n`).join(''));
  qr(srv, 'init'); qr(srv, 'commit', 'seed');
  const p = await port();
  const server = spawn(QR, ['--root', srv, 'serve', String(p)], { stdio: 'ignore' });
  t.after(() => server.kill());
  // ready when it accepts; the probe closes at once (the server serves one
  // request at a time, and an idle connection would hold it up)
  for (let i = 0; i < 100; i++) {
    const up = await new Promise((r) => { const c = net.connect(p, '127.0.0.1', () => { c.destroy(); r(true); }).on('error', () => r(false)); });
    if (up) break;
    await wait(50);
  }
  qr(a, 'clone', String(p)); qr(b, 'clone', String(p));
  qr(a, 'config', 'device', 'laptop'); qr(b, 'config', 'device', 'phone');

  const ua = recorder(), ub = recorder();
  let dirty = false;
  const ca = new Controller(a, { qr: new Qr(QR, a), ui: ua, policy: { minRemovedLines: 3 } });
  const cb = new Controller(b, { qr: new Qr(QR, b), ui: ub, isDirty: () => dirty });
  const file = (root, f) => path.join(root, f);
  const read = (root, f) => fs.readFileSync(file(root, f), 'utf8');

  // ordinary edits sync; the other device receives them
  fs.appendFileSync(file(a, 'todo.txt'), '- apples\n');
  await ca.tick();
  assert.strictEqual(ca.state.kind, 'synced');
  assert.ok(ca.state.result.committed && ca.state.result.pushed);
  // unsaved edits: wait, touch nothing
  dirty = true;
  await cb.tick();
  assert.strictEqual(cb.state.kind, 'waiting');
  dirty = false;
  await cb.tick();
  assert.strictEqual(cb.state.kind, 'synced');
  assert.match(read(b, 'todo.txt'), /- apples\n$/);

  // a deletion, and most of a file removed: held, announced once, nothing synced
  fs.unlinkSync(file(a, 'journal.txt'));
  const before = fs.readFileSync(path.join(srv, '.qrepo/HEAD'), 'utf8');
  await ca.tick();
  await ca.tick();
  assert.strictEqual(ca.state.kind, 'held');
  assert.deepStrictEqual(ca.state.held, [{ path: 'journal.txt', why: 'deleted' }]);
  assert.strictEqual(ua.held.length, 1);
  assert.strictEqual(fs.readFileSync(path.join(srv, '.qrepo/HEAD'), 'utf8'), before);
  // "sync anyway" sends it once; a new held set is announced again
  await ca.syncAnyway();
  assert.strictEqual(ca.state.kind, 'synced');
  await cb.tick();
  assert.ok(!fs.existsSync(file(b, 'journal.txt')));
  fs.writeFileSync(file(a, 'todo.txt'), 'only one line\n');
  ua.answer.held = Promise.resolve('sync');
  await ca.tick();
  await ca.idle(); await wait(50); await ca.idle();
  assert.strictEqual(ua.held.length, 2);
  assert.strictEqual(ca.state.kind, 'synced', 'answering "sync" from the toast sends it');
  await cb.tick();
  assert.strictEqual(read(b, 'todo.txt'), 'only one line\n');

  // the same line changed on both: announced in words, with picks
  fs.writeFileSync(file(a, 'todo.txt'), 'dentist on Tuesday\n- tea\n');
  await ca.tick();
  await cb.tick();
  fs.writeFileSync(file(a, 'todo.txt'), 'dentist on Friday\n- tea\n');
  await ca.tick();
  fs.writeFileSync(file(b, 'todo.txt'), 'dentist on Wednesday\n- tea\n');
  ub.answer.conflict = new Promise(() => {}); // a notification nobody answers
  await cb.tick();
  assert.strictEqual(cb.state.kind, 'conflict');
  assert.strictEqual(ub.conflicts.length, 1);
  assert.deepStrictEqual(ub.conflicts[0].picks, ['ours', 'theirs', 'both']);
  assert.deepStrictEqual(ub.conflicts[0].lines, [{ path: 'todo.txt', kind: 'text', says: 'you changed “Tuesday” to “Wednesday”; laptop changed it to “Friday”' }]);
  // the unanswered notification blocks nothing, and is not repeated
  await cb.tick();
  assert.strictEqual(cb.state.kind, 'conflict');
  assert.strictEqual(ub.conflicts.length, 1);
  // answered later: keep both, the merge finished and synced
  await cb.resolveAll('both');
  assert.strictEqual(cb.state.kind, 'synced');
  assert.strictEqual(read(b, 'todo.txt'), 'dentist on Friday\ndentist on Wednesday\n- tea\n');
  await ca.tick();
  assert.strictEqual(read(a, 'todo.txt'), read(b, 'todo.txt'));

  // compare, edit by hand, finish
  fs.writeFileSync(file(a, 'todo.txt'), 'call sam at 5\n- tea\n');
  await ca.tick();
  fs.writeFileSync(file(b, 'todo.txt'), 'call sam at 6\n- tea\n');
  ub.answer.conflict = Promise.resolve('compare');
  await cb.tick();
  await wait(20);
  assert.ok(ub.compared, 'compare opened');
  fs.writeFileSync(file(b, 'todo.txt'), 'call sam at 5 or 6\n- tea\n');
  await cb.tick();
  assert.strictEqual(cb.state.kind, 'conflict', 'a resolution in progress is left alone');
  assert.strictEqual(read(b, 'todo.txt'), 'call sam at 5 or 6\n- tea\n');
  await cb.finish();
  assert.strictEqual(cb.state.kind, 'synced');
  await ca.tick();
  assert.strictEqual(read(a, 'todo.txt'), 'call sam at 5 or 6\n- tea\n');

  // a server that is gone: offline, said once in the status, no notification
  server.kill();
  await wait(100);
  fs.appendFileSync(file(a, 'todo.txt'), '- offline note\n');
  await ca.tick();
  assert.strictEqual(ca.state.kind, 'offline');
  assert.match(ca.state.message, /remote|curl|connect/i);
  assert.deepStrictEqual(ua.errors, []);
});
