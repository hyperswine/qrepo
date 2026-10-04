'use strict';
// QRepo for VS Code: syncs a qrepo workspace on its own, holds unusual
// changes for a look, says when a conflict happens and offers to settle it,
// and shows on hover where each line came from.  All repository work is
// qr's; this file is the editor's side of it.
const fs = require('fs');
const path = require('path');
const vscode = require('vscode');
const { Qr } = require('./lib/qr');
const { Controller, PICKS } = require('./lib/controller');
const hover = require('./lib/hover');

const controllers = new Map(); // root -> { controller, timer, debounce }
let statusItem;
let contentProvider;

function settings() {
  const c = vscode.workspace.getConfiguration('qrepo');
  return {
    qrPath: c.get('path') || 'qr',
    interval: Math.max(5, c.get('syncInterval')),
    idleDelay: Math.max(1, c.get('idleDelay')),
    autoSync: c.get('autoSync'),
    hover: c.get('hover'),
    device: c.get('device'),
    mergeRules: c.get('mergeRules'),
    policy: {
      holdDeletions: c.get('hold.deletions'),
      holdBinary: c.get('hold.binary'),
      maxRemovedFraction: c.get('hold.removedFraction'),
      minRemovedLines: c.get('hold.removedLines'),
    },
  };
}

function isRoot(dir) { return fs.existsSync(path.join(dir, '.qrepo', 'config.json')); }
function rootOf(fsPath) {
  let best = null;
  for (const root of controllers.keys()) {
    if ((fsPath === root || fsPath.startsWith(root + path.sep)) && (!best || root.length > best.length)) best = root;
  }
  return best;
}
const rel = (root, fsPath) => path.relative(root, fsPath).split(path.sep).join('/');
const name = (root) => path.basename(root);

// ---- the UI the controller talks to -------------------------------------------------
const ui = {
  setState(root) { render(); },
  announceHeld(root, held) {
    const list = held.map((h) => `${h.path} (${h.why})`).join(', ');
    return vscode.window.showWarningMessage(
      `qrepo is holding changes in ${name(root)}: ${list}. Syncing them would reach your other devices.`,
      'Sync anyway', 'Review').then((c) => (c === 'Sync anyway' ? 'sync' : c === 'Review' ? 'review' : undefined));
  },
  async reviewHeld(root, held) {
    const pick = await vscode.window.showQuickPick(held.map((h) => ({ label: h.path, description: h.why, h })), { placeHolder: 'Held changes: pick one to look at' });
    if (!pick) return;
    if (pick.h.why === 'deleted') {
      const c = await vscode.window.showWarningMessage(`${pick.h.path} was deleted. Bring it back?`, 'Restore it', 'Keep it deleted');
      if (c === 'Restore it') {
        const r = await controllers.get(root).controller.qr.run('restore', 'HEAD', pick.h.path);
        if (r.code !== 0) vscode.window.showErrorMessage(`qrepo: ${(r.stderr || r.stdout).trim()}`);
        return controllers.get(root).controller.tick();
      }
      return;
    }
    return vscode.commands.executeCommand('vscode.diff', revUri(root, 'HEAD', pick.h.path), vscode.Uri.file(path.join(root, pick.h.path)), `${pick.h.path}: last sync ↔ now`);
  },
  announceConflict(root, record, lines, picks) {
    const other = record.remoteAuthor || 'another device';
    const what = lines.map((l) => `${l.path}: ${l.says}`).join('; ');
    const buttons = [...picks.map((p) => PICKS[p]), 'Compare'];
    return vscode.window.showWarningMessage(`Conflict just now with ${other} in ${what}`, ...buttons).then((c) => {
      const key = Object.keys(PICKS).find((k) => PICKS[k] === c);
      return key || (c === 'Compare' ? 'compare' : undefined);
    });
  },
  async compare(root, record) {
    const other = record.remoteAuthor || 'other device';
    const files = record.files.filter((f) => f.kind === 'text');
    for (const f of files.length ? files : record.files) {
      await vscode.commands.executeCommand('vscode.diff', revUri(root, record.remote, f.path), vscode.Uri.file(path.join(root, f.path)),
        `${f.path}: ${other}'s version ↔ yours (edit yours, then qrepo: Finish merge)`);
    }
  },
  error(root, message) { vscode.window.showErrorMessage(message); },
};

// ---- the status bar ---------------------------------------------------------------
function current() {
  const ed = vscode.window.activeTextEditor;
  const root = ed && ed.document.uri.scheme === 'file' ? rootOf(ed.document.uri.fsPath) : null;
  return root || controllers.keys().next().value;
}
function render() {
  const root = current();
  if (!root) return statusItem.hide();
  const st = controllers.get(root).controller.state;
  const label = controllers.size > 1 ? ` ${name(root)}` : '';
  const texts = {
    idle: ['$(sync)', ''], syncing: ['$(sync~spin)', ' syncing'], waiting: ['$(edit)', ' unsaved'],
    synced: ['$(check)', st.at ? ` ${hover.ago(st.at / 1000)}` : ''], local: ['$(check)', ' local only'],
    held: ['$(warning)', ` ${st.held ? st.held.length : ''} held`], conflict: ['$(error)', ' conflict'],
    behind: ['$(sync)', ' retrying'], offline: ['$(cloud)', ' offline'], error: ['$(error)', ' error'], paused: ['$(debug-pause)', ' paused'],
  };
  const [icon, words] = texts[st.kind] || ['$(sync)', ''];
  statusItem.text = `${icon} qrepo${label}${words}`;
  statusItem.tooltip = st.message || (st.kind === 'held' ? st.held.map((h) => `${h.path}: ${h.why}`).join('\n')
    : st.kind === 'conflict' ? `In conflict: ${st.record.files.map((f) => f.path).join(', ')}` : `qrepo: ${root}`);
  statusItem.backgroundColor = st.kind === 'conflict' || st.kind === 'error' ? new vscode.ThemeColor('statusBarItem.errorBackground')
    : st.kind === 'held' ? new vscode.ThemeColor('statusBarItem.warningBackground') : undefined;
  statusItem.show();
}

// ---- revisions as read-only documents: qrepo:/PATH?root=...&rev=... -----------------
function revUri(root, rev, p) {
  return vscode.Uri.from({ scheme: 'qrepo', path: '/' + p, query: new URLSearchParams({ root, rev }).toString() });
}
class RevisionProvider {
  async provideTextDocumentContent(uri) {
    const q = new URLSearchParams(uri.query);
    const root = q.get('root');
    const entry = controllers.get(root);
    if (!entry) return '';
    const r = await entry.controller.qr.show(q.get('rev'), uri.path.slice(1));
    return r.code === 0 ? r.stdout : '';
  }
}

// ---- hover: where a line came from ----------------------------------------------------
const blames = new Map(); // fsPath -> { key, blame }
async function blameFor(root, fsPath) {
  let key;
  try { key = `${fs.readFileSync(path.join(root, '.qrepo', 'HEAD'), 'utf8').trim()}:${fs.statSync(fsPath).mtimeMs}`; } catch { return null; }
  const hit = blames.get(fsPath);
  if (hit && hit.key === key) return hit.blame;
  const r = await controllers.get(root).controller.qr.blame(rel(root, fsPath));
  const blame = r.value || null;
  blames.set(fsPath, { key, blame });
  return blame;
}
const hoverProvider = {
  async provideHover(doc, position) {
    if (!settings().hover || doc.uri.scheme !== 'file') return null;
    const root = rootOf(doc.uri.fsPath);
    if (!root) return null;
    const st = controllers.get(root).controller.state;
    const record = st.kind === 'conflict' ? st.record : null;
    const p = rel(root, doc.uri.fsPath);
    const hunk = hover.conflictAt(record, p, position.line);
    // line numbers of unsaved text are not the file's: say only what is sure
    const info = doc.isDirty ? null : hover.lineInfo(await blameFor(root, doc.uri.fsPath), position.line);
    const text = hover.markdown(info, hunk, record);
    return text ? new vscode.Hover(new vscode.MarkdownString(text)) : null;
  },
};

// ---- roots and their loops ----------------------------------------------------------
async function applySettings(qr) {
  const s = settings();
  const cfg = await qr.config();
  if (s.device && cfg.device !== s.device) await qr.setConfig('device', s.device);
  const rules = s.mergeRules ? 'on' : 'off';
  if ((cfg['merge-rules'] || 'off') !== rules) await qr.setConfig('merge-rules', rules);
}
function start(root) {
  const s = settings();
  const qr = new Qr(s.qrPath, root);
  const controller = new Controller(root, {
    qr, ui, policy: s.policy,
    isDirty: (r) => vscode.workspace.textDocuments.some((d) => d.isDirty && d.uri.scheme === 'file' && rootOf(d.uri.fsPath) === r),
  });
  controller.paused = !s.autoSync;
  const entry = { controller, timer: null, debounce: null };
  entry.timer = setInterval(() => { if (!controller.paused) controller.tick(); else render(); }, s.interval * 1000);
  controllers.set(root, entry);
  applySettings(qr).then(() => (controller.paused ? controller.set({ kind: 'paused' }) : controller.tick()));
}
function stop(root) {
  const e = controllers.get(root);
  if (!e) return;
  clearInterval(e.timer);
  clearTimeout(e.debounce);
  controllers.delete(root);
}
function refreshRoots() {
  const want = new Set((vscode.workspace.workspaceFolders || []).map((f) => f.uri.fsPath).filter(isRoot));
  for (const root of [...controllers.keys()]) if (!want.has(root)) stop(root);
  for (const root of want) if (!controllers.has(root)) start(root);
  render();
}
// a save, then a quiet moment, then a tick
function soon(fsPath) {
  const root = rootOf(fsPath);
  const e = root && controllers.get(root);
  if (!e || e.controller.paused) return;
  clearTimeout(e.debounce);
  e.debounce = setTimeout(() => e.controller.tick(), settings().idleDelay * 1000);
}

function withRoot(fn) {
  return async () => {
    const root = current();
    if (!root) return vscode.window.showInformationMessage('qrepo: no qrepo folder is open');
    return fn(controllers.get(root).controller, root);
  };
}

async function menu(c, root) {
  const st = c.state;
  const items = [{ label: '$(sync) Sync now', run: () => c.tick(true) }];
  if (st.kind === 'held') items.push({ label: '$(warning) Sync held changes anyway', run: () => c.syncAnyway() }, { label: '$(eye) Review held changes', run: () => ui.reviewHeld(root, st.held) });
  if (st.kind === 'conflict') {
    for (const p of Object.keys(PICKS)) items.push({ label: `$(check) ${PICKS[p]}`, run: () => c.resolveAll(p) });
    items.push({ label: '$(diff) Compare and edit', run: () => ui.compare(root, st.record) }, { label: '$(git-merge) Finish merge (my edits are the resolution)', run: () => c.finish() });
  }
  items.push(c.paused ? { label: '$(play) Resume syncing', run: () => c.pause(false) } : { label: '$(debug-pause) Pause syncing', run: () => c.pause(true) });
  const pick = await vscode.window.showQuickPick(items, { placeHolder: `qrepo: ${name(root)} (${st.kind})` });
  if (pick) return pick.run();
}

function activate(context) {
  statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  statusItem.command = 'qrepo.menu';
  contentProvider = new RevisionProvider();
  context.subscriptions.push(
    statusItem,
    vscode.workspace.registerTextDocumentContentProvider('qrepo', contentProvider),
    vscode.languages.registerHoverProvider({ scheme: 'file' }, hoverProvider),
    vscode.workspace.onDidChangeWorkspaceFolders(refreshRoots),
    vscode.workspace.onDidSaveTextDocument((d) => soon(d.uri.fsPath)),
    vscode.window.onDidChangeActiveTextEditor(render),
    vscode.window.onDidChangeWindowState((w) => { if (w.focused) for (const e of controllers.values()) if (!e.controller.paused) e.controller.tick(); }),
    vscode.workspace.onDidChangeConfiguration((e) => { if (e.affectsConfiguration('qrepo')) { for (const r of [...controllers.keys()]) stop(r); refreshRoots(); } }),
    vscode.commands.registerCommand('qrepo.menu', withRoot(menu)),
    vscode.commands.registerCommand('qrepo.syncNow', withRoot((c) => c.tick(true))),
    vscode.commands.registerCommand('qrepo.syncAnyway', withRoot((c) => c.syncAnyway())),
    vscode.commands.registerCommand('qrepo.keepMine', withRoot((c) => c.resolveAll('ours'))),
    vscode.commands.registerCommand('qrepo.keepTheirs', withRoot((c) => c.resolveAll('theirs'))),
    vscode.commands.registerCommand('qrepo.keepBoth', withRoot((c) => c.resolveAll('both'))),
    vscode.commands.registerCommand('qrepo.compare', withRoot((c, root) => c.state.record ? ui.compare(root, c.state.record) : vscode.window.showInformationMessage('qrepo: no conflict'))),
    vscode.commands.registerCommand('qrepo.finishMerge', withRoot((c) => c.finish())),
    vscode.commands.registerCommand('qrepo.pause', withRoot((c) => c.pause(true))),
    vscode.commands.registerCommand('qrepo.resume', withRoot((c) => c.pause(false))),
    { dispose: () => { for (const r of [...controllers.keys()]) stop(r); } },
  );
  refreshRoots();
  return { controllers }; // for tests
}

function deactivate() {
  for (const r of [...controllers.keys()]) stop(r);
}

module.exports = { activate, deactivate };
