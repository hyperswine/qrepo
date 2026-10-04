'use strict';
// One qrepo root's sync loop.  Everything outside -- qr, the editor's UI,
// "are there unsaved edits", the clock -- comes in as `deps`, so the loop runs
// the same against VS Code and against a test.
//
// A tick:
//   1. unsaved edits under the root: wait (a pull could rewrite a file being
//      typed in; the next save brings a tick)
//   2. qr status --json.  Conflicts recorded: run sync anyway (it retries the
//      merge while the files are untouched, and touches nothing otherwise)
//   3. otherwise sort the changes: any held (policy.js) and nothing syncs
//      until the person allows it once
//   4. qr sync --json: synced, local, behind (try later) or conflict
// A conflict, or a held set, is announced once; the status bar keeps showing
// it.  Ticks never overlap.  An announcement is NOT awaited: a notification
// left unanswered stays pending, and must not stop syncing.  The answer comes
// back later as an action (syncAnyway, resolveAll, compare), which waits for
// any tick in progress before it acts.
const { classify, heldKey } = require('./policy');
const { describeFile } = require('./words');

const PICKS = { ours: 'Keep mine', theirs: 'Keep theirs', both: 'Keep both' };

class Controller {
  constructor(root, deps) {
    this.root = root;
    this.qr = deps.qr;
    this.ui = deps.ui;
    this.isDirty = deps.isDirty || (() => false);
    this.now = deps.now || (() => Date.now());
    this.policy = deps.policy || {};
    this.state = { kind: 'idle' };
    this.running = null;
    this.paused = false;
    this.allowOnce = false;
    this.announcedHeld = '';
    this.announcedConflict = '';
    this.lastSync = 0;
  }

  set(state) {
    this.state = state;
    this.ui.setState(this.root, state);
  }

  // one tick at a time; a tick asked for while one runs is folded into it
  tick(force = false) {
    if (this.running) return this.running;
    this.running = this._tick(force).catch((e) => this.set({ kind: 'error', message: String(e && e.message || e) }))
      .finally(() => { this.running = null; });
    return this.running;
  }

  async _tick(force) {
    if (this.paused && !force) return this.set({ kind: 'paused' });
    if (this.isDirty(this.root)) return this.set({ kind: 'waiting', message: 'unsaved changes' });
    const st = await this.qr.status();
    if (!st.value) return this.set({ kind: 'error', message: st.error || 'qr status failed' });
    if (!st.value.conflicts) {
      const { held } = classify(st.value.changes, this.policy);
      if (held.length && !this.allowOnce) {
        this.set({ kind: 'held', held });
        const key = heldKey(held);
        if (key !== this.announcedHeld) {
          this.announcedHeld = key;
          Promise.resolve(this.ui.announceHeld(this.root, held)).then((choice) => {
            if (choice === 'sync') return this.syncAnyway();
            if (choice === 'review') return this.ui.reviewHeld(this.root, held);
          });
        }
        return;
      }
    }
    this.allowOnce = false;
    this.announcedHeld = '';
    this.set({ kind: 'syncing' });
    const r = await this.qr.sync();
    if (!r.value) return this.set({ kind: 'offline', message: r.error || 'sync failed' });
    return this.after(r.value);
  }

  async after(result) {
    switch (result.state) {
      case 'synced':
      case 'local':
        this.lastSync = this.now();
        this.announcedConflict = '';
        return this.set({ kind: result.state, at: this.lastSync, result });
      case 'behind':
        return this.set({ kind: 'behind', message: 'the remote kept moving; trying again shortly' });
      case 'conflict':
        return this.conflict();
      default:
        return this.set({ kind: 'error', message: `unknown sync state ${result.state}` });
    }
  }

  async conflict() {
    const c = await this.qr.conflicts();
    const record = c.value;
    if (!record) return this.set({ kind: 'error', message: c.error || 'conflicts unreadable' });
    this.set({ kind: 'conflict', record });
    const key = `${record.local}:${record.remote}`;
    if (key === this.announcedConflict) return;
    this.announcedConflict = key;
    const other = record.remoteAuthor || 'the other device';
    const lines = record.files.map((f) => ({ path: f.path, kind: f.kind, says: describeFile(f, other) }));
    const textOnly = record.files.every((f) => f.kind === 'text');
    Promise.resolve(this.ui.announceConflict(this.root, record, lines, textOnly ? ['ours', 'theirs', 'both'] : ['ours', 'theirs'])).then((choice) => {
      if (choice in PICKS) return this.resolveAll(choice);
      if (choice === 'compare') return this.ui.compare(this.root, record);
    });
  }

  // wait for a tick in progress, then act
  async idle() {
    while (this.running) await this.running.catch(() => {});
  }

  // settle every conflicting file one way, finish the merge, sync it
  async resolveAll(pick) {
    await this.idle();
    const record = (await this.qr.conflicts()).value;
    if (!record) return this.tick(true);
    for (const f of record.files) {
      const r = await this.qr.resolve(f.path, f.kind === 'text' || pick !== 'both' ? pick : 'ours');
      if (r.code !== 0) {
        this.ui.error(this.root, `qrepo: ${(r.stderr || r.stdout).trim()}`);
        return this.set({ kind: 'conflict', record });
      }
    }
    return this.finish(`Resolve conflict: ${PICKS[pick].toLowerCase()}`);
  }

  // the person resolved by hand (or by picks): commit the merge and sync
  async finish(message = 'Resolve conflict') {
    await this.idle();
    const r = await this.qr.commitMerge(message);
    if (r.code !== 0) {
      this.ui.error(this.root, `qrepo: ${(r.stderr || r.stdout).trim()}`);
      return;
    }
    this.announcedConflict = '';
    return this.tick(true);
  }

  async syncAnyway() {
    await this.idle();
    this.allowOnce = true;
    return this.tick(true);
  }

  pause(on) {
    this.paused = on;
    this.set(on ? { kind: 'paused' } : { kind: 'idle' });
    if (!on) return this.tick();
  }
}

module.exports = { Controller, PICKS };
