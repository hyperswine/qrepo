# Experimental loopback remote

QRepo now has a native Base/POSIX server and CLI synchronization commands.
This is a local experiment, not an Internet-ready hosting service. It binds
only to **127.0.0.1**, has no authentication or TLS, and serves one repository.
The preserved Sol implementation is unchanged.

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

The server treats its repository as an **object store and canonical HEAD**;
accepted pushes do not update its working files. Inspect remote content using
`show`, or use a clone. Do not edit/commit directly in the serving root while
using it as the canonical store.

`remote PORT` configures an existing repository. `fetch` imports and verifies
remote objects and updates `REMOTE_HEAD`, without changing local HEAD or files.
`pull` fetches and fast-forwards or merges. `push` transfers local history and
requests an atomic, fast-forward HEAD update. Only localhost endpoints are
supported. `status` compares against the **last observed** remote HEAD, not a
live server query. Tags remain local and are not transferred.

## Concurrent edits and conflicts

A push includes the remote HEAD last observed by that client. While holding
the server repository lock, the worker compares that expectation to current
HEAD. A mismatch refuses the push. It also refuses replacing history with a
commit that does not descend from current HEAD. No force-push exists.

A JSON merge returns the numbers it was given: an integer past 62 bits is
carried as its text, not converted. It still rewrites the file in the
renderer's compact form, so a pretty-printed file comes back on one line.

On divergence, pull finds a unique nearest common ancestor. It merges file
identities first: changes to different files, identical edits, and uncontested
deletions merge automatically. When both sides modify an existing JSON file,
the existing recursive JSON merge policy combines disjoint fields. Arrays and
scalars remain atomic. Conflicting text/binary edits, delete/modify pairs,
concurrent differing additions, unrelated histories, or multiple merge bases
are refused. There is no project-schema validation hook wired into pull yet.

A refused conflict leaves local HEAD and working files unchanged, although
fetched objects and REMOTE_HEAD remain available. To resolve explicitly:

```sh
./qr --root /path/to/bob show HEAD state.json
# Read .qrepo/REMOTE_HEAD and use that ID with `show` for the remote version.
# Edit the working tree to the complete desired result, retaining any other
# independent remote changes as well.
./qr --root /path/to/bob commit-merge "Resolve conflict"
./qr --root /path/to/bob push
```

`commit-merge` records both the local and fetched remote heads as parents. It
is an explicit assertion that the caller has resolved the **whole working
tree**, not just one file; it does not invent a resolution or copy remaining
remote changes for you. Ordinary `commit` creates a single-parent checkpoint.

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

- `qr.fpr` implements all of it in FP-RISC: the protocol, framing, the
  listener, the workers, validation, graph traversal, synchronization, merge,
  and checkout recovery, over `std/tcp`, `std/stream` and `std/proc`.
- **Protocol 2.** Each connection carries one request and one reply. A message
  is frames, each a four-byte unsigned big-endian length and that many bytes:
  a header (UTF-8 JSON, with `version: 2` and `objects`, how many follow),
  then each object raw, as it is stored. An object carries no name: its
  identity is its hash. Requests have an `op` of `head`, `fetch`, or `push`.
  Version 1 (one JSON frame, objects in base64) is refused.
- **A transfer carries what the other side lacks.** A fetch says what the
  asker holds (`have`), a push leaves out what the server's head reaches. The
  receiver verifies every hash and the whole graph before any reference moves,
  so a sender that leaves out too much, or lies, changes nothing.
- Replies carry `version`, `ok`, and operation fields or an error. A request
  that fails ends only its worker; the peer sees the connection close, the
  listener goes on, and published HEAD is preserved.
- The listener handles one request at a time, each in a fresh process of the
  same executable (`Proc.self`), with the request on its stdin, the reply on
  its stdout and a twenty-second limit. What a request allocated goes with its
  process. This is not yet an actor-based server: a failed `check` ends a
  process, and an actor is not one.
- Limits: a frame is as long as its length field can say (4 GiB less one);
  five seconds of silence ends a read; twenty seconds ends a worker. A message
  is held in memory whole on both sides, so a transfer is bounded by memory,
  not by a number chosen here. No compression. No hostile peer
  resource-exhaustion guarantee is claimed.
- macOS arm64 is tested. `Proc.self` is implemented for Linux and FreeBSD and
  has not been run there.
- If a push succeeds but its reply is lost, fetch before retrying. Server state
  may already have advanced. Unreachable objects from refused/interrupted
  operations are retained; there is no garbage collector yet.

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
