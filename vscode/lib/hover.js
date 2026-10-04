'use strict';
// What a hover says about a line: which checkpoint wrote it, on which device,
// how long ago; and, in a conflict, the other device's version.

function ago(seconds, now = Date.now() / 1000) {
  const d = Math.max(0, Math.round(now - seconds));
  if (d < 60) return 'just now';
  const units = [[60, 'minute'], [3600, 'hour'], [86400, 'day'], [604800, 'week'], [2629800, 'month'], [31557600, 'year']];
  let [size, name] = units[0];
  for (const u of units) if (d >= u[0]) [size, name] = u;
  const n = Math.floor(d / size);
  return `${n} ${name}${n === 1 ? '' : 's'} ago`;
}

// the blame entry for a 0-based line: null if out of range
function lineInfo(blame, line) {
  if (!blame) return null;
  const range = blame.ranges.find((r) => line >= r.start && line < r.end);
  if (!range) return null;
  if (range.commit === null) return { committed: false };
  const c = blame.commits[range.commit] || {};
  return { committed: true, commit: range.commit, author: c.author, timestamp: c.timestamp, message: c.message };
}

// the conflict region of `path` that holds line (0-based, this device's numbering)
function conflictAt(record, path, line) {
  if (!record || !record.files) return null;
  const file = record.files.find((f) => f.path === path);
  if (!file || !file.hunks) return null;
  return file.hunks.find((h) => {
    const n = Math.max(1, h.ours.text.length);
    return line >= h.ours.line && line < h.ours.line + n;
  }) || null;
}

function markdown(info, hunk, record, now) {
  const parts = [];
  if (info && info.committed) {
    parts.push(`**${info.author}** · ${ago(info.timestamp, now)} · \`${info.commit.slice(0, 8)}\`  \n${info.message}`);
  } else if (info) {
    parts.push('**not synced yet**: this line is not in a checkpoint');
  }
  if (hunk) {
    const other = (record && record.remoteAuthor) || 'the other device';
    const theirs = hunk.theirs.text.map((l) => l.replace(/\n$/, '')).join('\n');
    parts.push(`**conflict** · ${other}'s version:\n\`\`\`\n${theirs || '(deleted)'}\n\`\`\``);
  }
  return parts.join('\n\n---\n\n');
}

module.exports = { ago, lineInfo, conflictAt, markdown };
