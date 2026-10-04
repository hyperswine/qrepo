# The remote

QRepo has a native Base/POSIX server and CLI synchronization commands. The
server speaks HTTP/1.1, checks a bearer token, and serves one repository. It
has no TLS: past a private network, put it behind a proxy that terminates TLS
(below: nginx on a public host, the server on a tailnet). The preserved Sol
implementation is unchanged.

## Run it

Create an existing empty directory for each repository, then:

```sh
./qr --root /path/to/server init
# Optional: add files and commit a seed checkpoint before serving.
./qr --root /path/to/server serve 2005

# In another terminal, with two existing empty directories:
./qr --root /path/to/alice clone 2005
./qr --root /path/to/bob clone 2005

# Edit files, then:
./qr --root /path/to/alice commit "Alice's change"
./qr --root /path/to/alice push
./qr --root /path/to/bob pull
```

A remote is a port (`http://127.0.0.1:PORT`) or an `http://` or `https://`
URL; requests go to `URL/qrepo`. `serve PORT` listens on 127.0.0.1;
`serve PORT --address ADDRESS` listens elsewhere, and then **requires a
token**:

```sh
./qr --root /path/to/server serve-token        # writes .qrepo/server-token, mode 0600
./qr --root /path/to/server serve 7600 --address 100.107.153.43

# each client takes the token on standard input, never as an argument
ssh server cat /path/to/server/.qrepo/server-token |
  ./qr --root /path/to/alice clone https://qrepo.example.com --token-stdin
./qr --root /path/to/alice remote https://qrepo.example.com --token-stdin < token
```

Once a server has a token it requires it on loopback too. The client keeps it
in `.qrepo/remote-token` (mode 0600) and hands it to curl in a header file.
A repository configured before URLs (`remote.json` with only a port) still
works, as `http://127.0.0.1:PORT`. A clone that fails after initializing
leaves a repository with its remote configured: `pull` finishes it.

The server treats its repository as an **object store and canonical HEAD**;
accepted pushes do not update its working files. Inspect remote content using
`show`, or use a clone. Do not edit/commit directly in the serving root while
using it as the canonical store.

`remote REMOTE` configures an existing repository. `fetch` imports and verifies
remote objects and updates `REMOTE_HEAD`, without changing local HEAD or files.
`pull` fetches and fast-forwards or merges. `push` transfers local history and
requests an atomic, fast-forward HEAD update. `status` compares against the
**last observed** remote HEAD, not a live server query. Tags remain local and
are not transferred. Nothing syncs on its own: each device commits, pushes
and pulls when told.

## Concurrent edits and conflicts

A push includes the remote HEAD last observed by that client. While holding
the server repository lock, the worker compares that expectation to current
HEAD. A mismatch refuses the push. It also refuses replacing history with a
commit that does not descend from current HEAD. No force-push exists.

A JSON merge returns the numbers it was given: an integer past 62 bits is
carried as its text, not converted. It still rewrites the file in the
renderer's compact form, so a pretty-printed file comes back on one line.

On divergence, pull finds a unique nearest common ancestor and merges file by
file. Changes to different files, identical edits, and uncontested deletions
merge as before. When both sides changed one file:

- **JSON** (all three versions parse): the recursive JSON merge combines
  disjoint fields; arrays and scalars are atomic.
- **Text** (no NUL byte): a line merge (`text.fpr`). Each side is a list of
  edits against the base, found by patience diff. Edits apart from each other
  merge, and so do identical ones. Edits that truly overlap (both changed a
  line, or one inserted inside what the other replaced) conflict. Edits that
  only **touch** (adjacent lines, an insertion at the edge of the other's
  edit, or both inserting at one place) conflict too, as in git, unless
  `qr config merge-rules on`: then they are applied in order, and lines both
  inserted at one place go **the remote's first** (they reached the server
  first; commit times are not used, since clocks differ and seconds tie). A
  file added on both sides merges as text from an empty base.
- **Binary**, delete/modify, unrelated histories and multiple merge bases
  conflict.

**Every** conflicting file is found and recorded together in
`.qrepo/conflicts.json`, which `qr conflicts` prints (`--json` for an editor):
for each file its kind and, for text, each conflicting region's lines in the
base, this device's version ("ours", with its line number) and the remote's
("theirs"). A conflict leaves local HEAD and working files unchanged, although
fetched objects and REMOTE_HEAD remain available. The record goes when a later
pull merges (say, after the rules are turned on) or `commit-merge` resolves
it. To resolve by hand:

```sh
./qr --root /path/to/bob conflicts
./qr --root /path/to/bob show <remote id from conflicts> todo.txt
# Edit the working tree to the complete desired result, retaining any other
# independent remote changes as well.
./qr --root /path/to/bob commit-merge "Resolve conflict"
./qr --root /path/to/bob push
```

`qr resolve PATH ours|theirs|both` writes a conflicting file as the merge
would have made it, each conflicting region taken from this device's version,
the remote's, or both (the remote's lines first); a file that is not text
takes one side whole. With conflicts recorded, `commit-merge` finishes the
merge: the conflicting paths as the working tree has them, every other path
as the merge made it (the remote's changes elsewhere come along and are
written out), both heads as parents. Anything else changed in the working
tree refuses it. Without a record, `commit-merge` commits the working tree as
the whole resolution, as before. Ordinary `commit` creates a single-parent
checkpoint.

## Sync, for an editor

`qr sync` is what an editor runs on a timer: commit whatever changed (as
`auto: todo.txt +3 -1, A new.txt`), pull (merging), push, and on a push
refused because the remote moved meanwhile, pull and push once more. With
`--json` it answers `{"state", "committed", "pulled", "pushed", "head",
"conflicts"}`; state is `synced`, `local` (no remote), `behind` (the remote
kept moving: try later) or `conflict` (exit status 3). With conflicts
recorded it commits nothing: while the working tree is as HEAD left it, it
tries the merge again; once someone edits a resolution, it leaves the files
alone until `commit-merge`.

Deciding whether a change is ordinary enough to commit unasked is the
editor's: `qr status --json` measures each changed path (`change` A/M/D,
`text`, lines `added` and `removed`, and the `lines` the checkpoint had)
without reading a binary file whole. `qr blame PATH --json` gives, for each
run of lines, the checkpoint that wrote them and its device, author and time;
lines not yet committed have none. Commits carry the device name set with
`qr config device NAME` (`local` until then); `config` is per device and is
never synced.

## Checkout and recovery

Pull refuses dirty included working files and protects incoming paths from
overwriting ignored/untracked content. It validates incoming object hashes,
kinds, graph closure, and safe tree paths before publishing references.

Working files cannot all be replaced atomically by ordinary POSIX rename.
Instead, checkout writes a durable `.qrepo/checkout.json` plan, applies file
changes idempotently, publishes HEAD last, then removes the journal. A following
command resumes an interrupted checkout; `recover` does so explicitly. If an
outside edit no longer matches either old or new content, recovery stops rather
than replacing it silently. Mid-checkout observers can see a mixed working
tree. Deletions are applied first and a directory that loses its last file is
removed, so a checkpoint may put a file where a directory stood and the
reverse. A directory that holds anything the checkpoint being replaced does not
account for (untracked, ignored or edited) is not replaced: the pull is refused
before anything is written, and names the file. Directory metadata and empty
directories are not versioned.

Repository locks coordinate QRepo processes, not ordinary editors. The existing
external-edit and hostile-directory-rename limitations still apply. Process
termination was tested; physical power failure was not.

## Transport and implementation

- `qr.fpr` implements all of it in FP-RISC: the protocol, framing, the HTTP
  listener, the workers, validation, graph traversal, synchronization, merge,
  and checkout recovery, over `std/tcp`, `std/stream` and `std/proc`. The
  client runs `curl` for each request, because std has no TLS.
- **Protocol 3.** A request is one HTTP `POST /qrepo` whose body is a
  message; the reply's body is another. A message is frames, each a four-byte
  unsigned big-endian length and that many bytes: a header (UTF-8 JSON, with
  `version: 3` and `objects`, how many follow), then each object raw, as it is
  stored. An object carries no name: its identity is its hash. Requests have
  an `op` of `head`, `fetch`, or `push`. Versions 1 and 2 (raw TCP) are
  refused. Requests need `Content-Length`; chunked bodies are refused (411).
- **Nothing holds a transfer in memory.** The client assembles a request in
  `.qrepo/spool/client/` and curl uploads it from there (`-T`, which streams)
  and writes the reply there. The listener writes a request body to
  `.qrepo/spool/serve/` in 64 KiB pieces, the worker reads it and writes its
  reply beside it, and the listener sends that back in pieces. Objects move
  between those files and the store in `posix.c`; only headers are parsed in
  FP-RISC. Each connection, and each piece, runs in an arena of its own.
- **A transfer carries what the other side lacks.** A fetch says what the
  asker holds (`have`), a push leaves out what the server's head reaches. The
  receiver hashes every object as it stores it and checks the whole graph
  before any reference moves, so a sender that leaves out too much, or lies,
  changes nothing.
- Replies carry `version`, `ok`, and operation fields or an error. A request
  whose worker fails gets `500` with the worker's message; the listener goes
  on, and published HEAD is preserved. `401` is a missing or wrong token,
  `404`/`405` is anything but `POST /qrepo`.
- The listener handles one request at a time, each in a fresh process of the
  same executable (`Proc.self`). A client that connects and then sends
  nothing holds it for the 30 s silence limit; behind nginx that cannot
  happen (nginx connects only with a request to forward), on a bare port it
  can. The worker has no time limit: it reads local
  files, not the network. This is not yet an actor-based server: a failed
  `check` ends a process, and an actor is not one.
- Limits, and where they come from: a frame, so an object, is as long as its
  length field can say (4 GiB less one). A request head is refused past
  64 KiB (431); a proxy in front bounds it far lower. Thirty seconds of
  silence from a peer ends its request on the server; on the client, curl
  gives up on a connection after 20 s and on a transfer that moves nothing
  for 300 s (a pushed transfer is silent while the server verifies it; the
  nginx site allows the same). No compression. No hostile-peer resource-exhaustion guarantee is
  claimed beyond those: the token and the proxy are what keep strangers out.
- macOS arm64 is tested. `Proc.self` is implemented for Linux and FreeBSD and
  has not been run there.
- If a push succeeds but its reply is lost, fetch before retrying. Server state
  may already have advanced. Unreachable objects from refused/interrupted
  operations are retained until `gc`.

## Behind nginx, over a tailnet

The layout this was built for: a public host runs nginx with a Let's Encrypt
certificate for the domain, is on the same tailnet as the machine that holds
the repository, and forwards `/qrepo` to it. TLS ends at nginx; the hop over
the tailnet is plain HTTP inside WireGuard; QRepo checks the token itself.

- `deploy/qrepo.cswine.cloud.nginx`: the site. No body-size cap
  (`client_max_body_size 0`), request and response buffering off so transfers
  stream instead of spooling on the proxy, and five-minute read/send
  timeouts, which apply between reads, not to a whole transfer. Everything
  but `/qrepo` is a 404.
- `deploy/cloud.cswine.qrepo.plist`: a LaunchAgent that runs
  `qr serve 7600 --address <tailnet address>` and restarts it, including when
  the tailnet is not up yet at login.

The server's repository needs `init` and `serve-token` before the agent
starts. Clients then `clone https://DOMAIN --token-stdin`.

## Reproduce the experiment

```sh
./test.sh
python3 tests/soak.py /new/experiment-directory --seconds 180 --port 2005
```

The test suite covers two clones, binary transfer, stale and simultaneous pushes,
independent files/JSON fields, deletions, explicit conflict resolution, dirty
working copies, corrupt/incomplete objects, unsafe and reserved paths, broken
and truncated frames, negotiation, listener survival, restart, interrupted
checkout recovery, a peer that lies, and objects and histories past 16 MiB.

The soak script creates `server`, `alice`, and `bob`, and performs twelve rounds
spaced across three minutes. Every round creates divergent commits, tests a
stale push, reconciles, and checks convergence. Every fourth round creates a
same-field conflict and resolves it explicitly; every third round tests dirty
pull refusal. It audits every reachable object hash, both-parent commit history,
and both working trees at the end. This is a correctness experiment with idle
intervals, not a saturation/load test.

It leaves the successful server running. The directory contains `server.pid`,
`server-command.json`, `server.log`, `commands.jsonl`, and `RESULTS.json`. The
service is a background process, not a login/startup service. Stop that recorded
server process when done; rerun its recorded command to restart it.
