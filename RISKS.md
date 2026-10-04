# Risks

What could go wrong with QRepo as a documents sync (`https://qrepo.cswine.cloud`:
nginx on the Linode, over the tailnet, to the mini; the VS Code extension on
each Mac). Written 2026-10-04; each entry says what was done about it.

## On a clock

1. **The certificate renewal did not reload nginx.** certbot renews
   `qrepo.cswine.cloud` (webroot, no installer) about 30 days before
   2027-01-02, and nginx keeps serving the certificate it loaded. The
   Linode's deploy hooks reloaded nginx only for logbook, manual and storage.
   *Done:* a deploy hook for qrepo (below, Status).
2. **Tailscale node keys expire** (macm4 2027-03-07, localhost-0 2027-03-16,
   the mini 2027-03-25). When the Linode's or the mini's lapses, every site
   proxied over the tailnet stops. *Done:* see Status.

## Easy to get wrong

3. **A clone inside iCloud Drive or Dropbox.** Two sync systems writing one
   `.qrepo` duplicate files ("todo 2.txt") and iCloud can evict files to
   placeholders. With "Desktop & Documents" on, that includes `~/Documents`.
   Keep clones elsewhere (`~/Docs`).
4. **Editing outside VS Code.** The extension waits only for VS Code's
   unsaved buffers. Another app holding a file with unsaved edits across a
   pull saves the old text back; qrepo sees an ordinary edit that undoes the
   other device's change and syncs it. Recoverable from history, unannounced.
5. **Other apps' scratch files.** Office `~$name.docx`, LibreOffice
   `.~lock.name#`, Vim `*.swp`, AppleDouble `._name` and the like were
   versioned and synced. *Done:* `qr init` writes a default `.qrepoignore`;
   the live store has it.

## Over months

6. **Every sync walked the whole history.** Ancestry (status, pull, push,
   merge base) collected every ancestor; fetch, push and checkout checked
   the whole graph complete, parsing every tree; the server found what a
   client had by walking all of the client's history. Cost grew with commits
   times files, and the extension syncs every minute. *Done:* generation
   numbers and verified-complete marks (README, "History at scale").
7. **Storage only grows.** Every saved version of every file is kept, on the
   mini and in every clone (a clone takes all history). `gc` removes only
   what nothing reaches. No pruning, no shallow clone.
8. **The merge rules can be wrong without saying so.** Right by lines, not by
   meaning: a CSV header changed next to an added row merges. A merged JSON
   file comes back on one line.
9. **Blame walks to where each line began**, parsing a tree per commit; on a
   long history a hover can take seconds (cached per HEAD).

## Dependencies

10. **fprisc moves.** The 0-based change broke `qr` once. Devices merge on
    pull, so two devices on different builds can merge differently. Build
    every device's `qr` from one fprisc commit, and record it.
11. **Only macOS arm64 has run.** `curl` on each client, OpenSSL on Linux,
    and the Linux and FreeBSD builds are untested. A `qr` copied by AirDrop or
    a browser gets the quarantine flag and will not run; `scp` does not set it.

## Security

12. **A folder's own settings could choose the `qr` the extension runs.**
    `qrepo.path` was read from workspace settings too, so a folder with
    `.qrepo/config.json` and a `.vscode/settings.json` naming its own program
    would run it when opened (workspace trust, on here, blocks untrusted
    folders). *Done:* `qrepo.path` and `qrepo.device` are machine settings,
    which a workspace cannot set.
13. **One bearer token is the only lock**, the same on every device, in plain
    text in each clone's `.qrepo`. No rotation command: delete
    `.qrepo/server-token`, run `serve-token`, restart, and give every device
    the new one (`qr remote URL --token-stdin`).

## By design, but surprising

- The server takes one request at a time: a large push makes the others wait.
- Held changes hold pulls too: a device stays behind until they are settled.
- UTF-16 text has NUL bytes and is binary: it never merges. A change of line
  endings changes every line.
- **The mini's network is everyone's.** On 2026-10-04 the mini dropped off
  the tailnet for about an hour (it stayed up; restarting the router brought
  it back), and qrepo, pos and qchat answered 504 meanwhile. Nothing is
  lost (devices keep working and sync when it returns), but nothing
  watches for it either: no alert, no restart of the network side.
- The mini is the only server. Every clone holds all history and is a
  reasonable backup, but tags, and what was never pushed, live only where
  they were made.

## Status

| # | What | State |
|---|---|---|
| 1 | certbot deploy hook for qrepo | **done**: `/etc/letsencrypt/renewal-hooks/deploy/qrepo-nginx.sh`, run by hand (nginx reloaded) and a renewal dry run passed. **pos, qchat and vpply have the same gap** (no installer, no hook); today another site's renewal happens to reload nginx before they expire. |
| 2 | Tailscale key expiry off for the Linode and the mini | pending: the admin console needs the owner signed in |
| 5 | default `.qrepoignore` | **done**: `qr init` writes `default.qrepoignore`; an empty repository pulling takes the remote's rules over it; the live store has it (commit `3e7fa05f`) |
| 6 | history-bounded sync | **done**: generation numbers and verified marks (README, History at scale); the mini runs it since 2026-10-04 (previous build kept as `~/qrepo/qr.prev`) |
| 12 | machine-scoped extension settings | **done**: `qrepo.path` and `qrepo.device` are `machine` settings (extension 0.1.1) |
