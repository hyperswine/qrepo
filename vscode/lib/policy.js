'use strict';
// Which changes are ordinary enough to sync without asking.  The rest are
// HELD: nothing syncs (a sync commits everything) until the person says so,
// because a held change would reach every other device on the next sync.
// Nothing is lost either way -- history keeps every version -- this is about
// not spreading a mistake.

const defaults = {
  holdDeletions: true,       // a deleted file
  holdBinary: false,         // a changed binary file
  maxRemovedFraction: 0.5,   // removing more than this share of a file...
  minRemovedLines: 10,       // ...when that is at least this many lines
};

function why(change, opts) {
  const o = { ...defaults, ...opts };
  if (change.change === 'D') return o.holdDeletions ? 'deleted' : null;
  if (!change.text) return o.holdBinary && change.change === 'M' ? 'binary file changed' : null;
  if (change.change === 'M' && change.removed >= o.minRemovedLines && change.removed > o.maxRemovedFraction * change.lines) {
    return `${change.removed} of ${change.lines} lines removed`;
  }
  return null;
}

function classify(changes, opts) {
  const ordinary = [], held = [];
  for (const c of changes || []) {
    const reason = why(c, opts);
    if (reason) held.push({ path: c.path, why: reason }); else ordinary.push(c);
  }
  return { ordinary, held };
}

// a stable name for a set of held changes, so the same set is announced once
function heldKey(held) {
  return held.map((h) => `${h.path}:${h.why}`).sort().join('|');
}

module.exports = { classify, heldKey, defaults };
