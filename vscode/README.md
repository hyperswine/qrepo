# QRepo for VS Code

Open a folder that is a qrepo clone (it has `.qrepo/config.json`) and the
extension starts by itself. It does four things:

- **Syncs on its own.** A save, then `qrepo.idleDelay` seconds of quiet, and
  every `qrepo.syncInterval` seconds besides: `qr sync`, which commits what
  changed (`auto: todo.txt +3 -1`), pulls, merges and pushes. Nothing runs
  while a file in the folder has unsaved edits.
- **Holds unusual changes.** A deleted file, or a text file that lost most
  of its lines (`qrepo.hold.*`), would reach every other device on the next
  sync, so nothing syncs until you say so: *Sync anyway*, or *Review*
  (restore a deleted file, or compare with the last sync). History keeps
  every version either way.
- **Settles conflicts with you.** Edits apart from each other merge by
  themselves. With `qrepo.mergeRules` on, so do edits that only touch
  (adjacent lines, both appending: the other device's lines go first). When
  both devices changed the same line, a notification says what happened
  (*you changed “Tuesday” to “Wednesday”; laptop changed it to “Friday”*)
  and offers *Keep mine*, *Keep theirs*, *Keep both*, or *Compare*: the other
  device's version beside yours; edit yours, then *qrepo: Finish merge*.
  Your files are not touched until you choose.
- **Explains lines on hover**: which device wrote a line, when, and in which
  checkpoint; in a conflict, the other device's version of it.

The status bar shows where the folder stands (synced 2 minutes ago, unsaved,
held, conflict, offline, paused); click it for the actions.

## Setup

The extension runs `qr`; set `qrepo.path` to it if it is not on PATH, and
`qrepo.device` to this machine's name. Clone first, on the command line:

```sh
ssh mini 'cat ~/qrepo-docs/.qrepo/server-token' | qr --root ~/Docs clone https://qrepo.example.com --token-stdin
```

## Development

```sh
npm test                 # policy, wording, hover; the sync loop against a real qr
node test/run-vscode.js  # the extension inside an isolated VS Code (a window opens)
npm run package          # qrepo-VERSION.vsix, without vsce
code --install-extension qrepo-0.1.0.vsix
```
