'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { classify, heldKey } = require('../lib/policy');
const { describeHunk, describeFile, narrow } = require('../lib/words');
const hover = require('../lib/hover');
const { Controller } = require('../lib/controller');

const M = (path, added, removed, lines, text = true) => ({ path, change: 'M', text, added, removed, lines });

test('policy: ordinary edits sync, unusual ones are held', () => {
  const { ordinary, held } = classify([
    M('notes.txt', 3, 1, 40), { path: 'old.txt', change: 'D', text: true, added: 0, removed: 9, lines: 9 },
    M('big.txt', 0, 30, 40), M('small.txt', 0, 4, 5), M('img.png', 0, 0, 0, false), { path: 'new.txt', change: 'A', text: true, added: 2, removed: 0, lines: 0 },
  ]);
  assert.deepStrictEqual(ordinary.map((c) => c.path), ['notes.txt', 'small.txt', 'img.png', 'new.txt']);
  assert.deepStrictEqual(held, [{ path: 'old.txt', why: 'deleted' }, { path: 'big.txt', why: '30 of 40 lines removed' }]);
  assert.deepStrictEqual(classify([{ path: 'x', change: 'D', text: true }], { holdDeletions: false }).held, []);
  assert.strictEqual(classify([M('img.png', 0, 0, 0, false)], { holdBinary: true }).held[0].why, 'binary file changed');
  assert.strictEqual(heldKey([{ path: 'b', why: 'x' }, { path: 'a', why: 'y' }]), heldKey([{ path: 'a', why: 'y' }, { path: 'b', why: 'x' }]));
});

const H = (b, o, t, line = 2) => ({ base: { line, text: b }, ours: { line, text: o }, theirs: { line, text: t } });

test('words: a conflict said the way a person would', () => {
  assert.deepStrictEqual(narrow('- eggs\n', '- 12 eggs\n', '- 6 eggs\n'), ['eggs', '12 eggs', '6 eggs']);
  assert.strictEqual(describeHunk(H(['- dentist on Tuesday\n'], ['- dentist on Wednesday\n'], ['- dentist on Friday\n']), 'laptop'),
    'you changed “Tuesday” to “Wednesday”; laptop changed it to “Friday”');
  assert.strictEqual(describeHunk(H([], ['- plums\n'], ['- pears\n']), 'laptop'), 'you added “- plums”; laptop added “- pears” in the same place');
  assert.strictEqual(describeHunk(H(['x\n'], [], ['y\n']), 'phone'), 'you deleted “x”; phone changed it to “y”');
  assert.strictEqual(describeHunk(H(['x\n'], ['z\n'], []), 'phone'), 'you changed “x” to “z”; phone deleted it');
  assert.strictEqual(describeHunk(H(['a\n', 'b\n'], ['A\n', 'B\n'], ['a2\n', 'b2\n'], 4)), 'you changed lines 5–6; the other device changed them too');
  assert.match(describeHunk(H(['k ' + 'w '.repeat(60) + '\n'], ['K ' + 'w '.repeat(60) + 'X\n'], ['k ' + 'w '.repeat(60) + 'Y\n'])), /…/);
  assert.strictEqual(describeFile({ kind: 'text', hunks: [H(['a\n'], ['b\n'], ['c\n']), H(['d\n'], ['e\n'], ['f\n'])] }, 'phone'), 'you changed “a” to “b”; phone changed it to “c” (and 1 more)');
  assert.strictEqual(describeFile({ kind: 'binary', hunks: [] }, 'phone'), 'you and phone both changed it');
});

test('hover: where a line came from, and the other side of a conflict', () => {
  const now = 1_000_000;
  assert.strictEqual(hover.ago(now - 5, now), 'just now');
  assert.strictEqual(hover.ago(now - 60, now), '1 minute ago');
  assert.strictEqual(hover.ago(now - 7200, now), '2 hours ago');
  assert.strictEqual(hover.ago(now - 86400 * 3, now), '3 days ago');
  const blame = { ranges: [{ start: 0, end: 2, commit: 'abcdef0123' }, { start: 2, end: 3, commit: null }],
    commits: { abcdef0123: { author: 'phone', timestamp: now - 120, message: 'auto: todo.txt +1 -0' } } };
  assert.deepStrictEqual(hover.lineInfo(blame, 1), { committed: true, commit: 'abcdef0123', author: 'phone', timestamp: now - 120, message: 'auto: todo.txt +1 -0' });
  assert.deepStrictEqual(hover.lineInfo(blame, 2), { committed: false });
  assert.strictEqual(hover.lineInfo(blame, 9), null);
  const md = hover.markdown(hover.lineInfo(blame, 0), null, null, now);
  assert.match(md, /\*\*phone\*\* · 2 minutes ago · `abcdef01`/);
  const record = { remoteAuthor: 'laptop', files: [{ path: 'todo.txt', kind: 'text', hunks: [H(['- eggs\n'], ['- 12 eggs\n'], ['- 6 eggs\n'], 3)] }] };
  assert.strictEqual(hover.conflictAt(record, 'todo.txt', 3).theirs.text[0], '- 6 eggs\n');
  assert.strictEqual(hover.conflictAt(record, 'todo.txt', 4), null);
  assert.strictEqual(hover.conflictAt(record, 'other.txt', 3), null);
  assert.match(hover.markdown(null, hover.conflictAt(record, 'todo.txt', 3), record), /laptop's version:\n```\n- 6 eggs\n```/);
});

// a qr that answers from a script and records what it was asked
function fakeQr(status, calls) {
  return {
    status: async () => { calls.push('status'); return { value: status() }; },
    sync: async () => { calls.push('sync'); return { value: { state: 'synced' } }; },
    conflicts: async () => { calls.push('conflicts'); return { value: { local: 'l', remote: 'r', files: [] } }; },
  };
}
const quietUi = { setState() {}, announceHeld() {}, announceConflict() {}, reviewHeld() {}, compare() {}, error() {} };

test('flush on closing: saved edits go out; nothing changed, paused or held sends nothing', async () => {
  const edits = [M('notes.txt', 2, 0, 19)];
  let calls = [];
  const c = new Controller('/r', { qr: fakeQr(() => ({ changes: edits, conflicts: false }), calls), ui: quietUi });
  assert.strictEqual(await c.flush(), 'synced');
  assert.deepStrictEqual(calls, ['status', 'status', 'sync']);

  calls = [];
  const idle = new Controller('/r', { qr: fakeQr(() => ({ changes: [], conflicts: false }), calls), ui: quietUi });
  assert.strictEqual(await idle.flush(), 'nothing');
  assert.deepStrictEqual(calls, ['status']);

  calls = [];
  const paused = new Controller('/r', { qr: fakeQr(() => ({ changes: edits, conflicts: false }), calls), ui: quietUi });
  paused.paused = true;
  assert.strictEqual(await paused.flush(), 'paused');
  assert.deepStrictEqual(calls, []);

  // a held deletion is not pushed through on the way out
  calls = [];
  const held = new Controller('/r', { qr: fakeQr(() => ({ changes: [{ path: 'old.txt', change: 'D', text: true, added: 0, removed: 9, lines: 9 }], conflicts: false }), calls), ui: quietUi });
  assert.strictEqual(await held.flush(), 'held');
  assert.ok(!calls.includes('sync'));

  // a tick already running is waited for, not doubled
  calls = [];
  const busy = new Controller('/r', { qr: fakeQr(() => ({ changes: edits, conflicts: false }), calls), ui: quietUi });
  const first = busy.tick();
  const flushed = busy.flush();
  await first;
  assert.strictEqual(await flushed, 'synced');
  assert.deepStrictEqual(calls, ['status', 'sync', 'status', 'status', 'sync']);
});
