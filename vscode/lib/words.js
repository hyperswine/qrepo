'use strict';
// Saying what a conflict is, in words: "you changed “eggs” to “12 eggs”;
// laptop changed it to “6 eggs”".

function clip(s, n = 60) {
  s = s.replace(/\s+/g, ' ').trim();
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}
const q = (s) => `“${clip(s)}”`;
const joined = (lines) => lines.map((l) => l.replace(/\n$/, '')).join(' / ');

// the part of each version between the words all three share at either end
function narrow(base, ours, theirs) {
  const split = (s) => s.split(/(\s+)/).filter((t) => t !== '');
  const [b, o, t] = [split(base), split(ours), split(theirs)];
  let pre = 0;
  while (pre < b.length && pre < o.length && pre < t.length && b[pre] === o[pre] && b[pre] === t[pre]) pre++;
  let suf = 0;
  while (suf < b.length - pre && suf < o.length - pre && suf < t.length - pre &&
         b[b.length - 1 - suf] === o[o.length - 1 - suf] && b[b.length - 1 - suf] === t[t.length - 1 - suf]) suf++;
  // when the base kept nothing between (both inserted words), widen by the
  // word that follows, or else the one before: "eggs" -> "12 eggs"
  const word = (x) => !/^\s+$/.test(x);
  if (b.length - pre - suf <= 0) {
    if (suf > 0) { suf--; while (suf > 0 && !word(b[b.length - 1 - suf])) suf--; }
    else if (pre > 0) { pre--; while (pre > 0 && !word(b[pre])) pre--; }
  }
  const mid = (xs) => xs.slice(pre, xs.length - suf).join('').trim();
  return [mid(b), mid(o), mid(t)];
}

function describeHunk(hunk, other = 'the other device') {
  const [bl, ol, tl] = [hunk.base.text, hunk.ours.text, hunk.theirs.text];
  if (bl.length === 0) return `you added ${q(joined(ol))}; ${other} added ${q(joined(tl))} in the same place`;
  if (ol.length === 0) return `you deleted ${q(joined(bl))}; ${other} changed it to ${q(joined(tl))}`;
  if (tl.length === 0) return `you changed ${q(joined(bl))} to ${q(joined(ol))}; ${other} deleted it`;
  if (bl.length === 1 && ol.length === 1 && tl.length === 1) {
    const [b, o, t] = narrow(bl[0], ol[0], tl[0]);
    return b ? `you changed ${q(b)} to ${q(o)}; ${other} changed it to ${q(t)}`
             : `you wrote ${q(o)}; ${other} wrote ${q(t)}`;
  }
  const at = hunk.ours.line + 1, end = hunk.ours.line + ol.length;
  return `you changed line${end > at ? `s ${at}–${end}` : ` ${at}`}; ${other} changed ${bl.length > 1 ? 'them' : 'it'} too`;
}

function describeFile(file, other) {
  if (file.kind === 'text' && file.hunks.length) {
    const first = describeHunk(file.hunks[0], other);
    return file.hunks.length > 1 ? `${first} (and ${file.hunks.length - 1} more)` : first;
  }
  if (file.kind === 'delete/modify') return `one side deleted it, the other changed it`;
  if (file.kind === 'binary') return `you and ${other} both changed it`;
  return file.why;
}

module.exports = { describeHunk, describeFile, narrow, clip };
