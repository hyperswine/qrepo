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
tree. Directory/file shape transitions can currently be refused. Directory
metadata and empty directories are not versioned.

Repository locks coordinate QRepo processes, not ordinary editors. The existing
external-edit and hostile-directory-rename limitations still apply. Process
termination was tested; physical power failure was not.

## Transport and implementation

- `qr.fpr` implements the protocol, validation, graph traversal, synchronization,
  merge, and checkout recovery in FP-RISC.
- `network.c` implements framed loopback TCP and native worker process launching.
- Each connection carries one request and one response: a four-byte unsigned
  big-endian length followed by UTF-8 JSON. Requests carry `version: 1` and an
  `op` of `head`, `fetch`, or `push`. Object bytes are base64 inside JSON.
- Responses carry `version`, `ok`, and operation fields or an error. Invalid
  payloads can terminate just their worker; clients then receive a connection
  error. The listener survives and published HEAD is preserved.
- The server handles one request at a time in a fresh **native executable**
  process, reclaiming its allocations afterward. No Sol, Python, or compiler
  participates in serving requests. This is not yet an actor-based server.
- Experimental limits: 16 MiB per frame, five-second socket I/O timeouts,
  twenty-second worker deadline, backlog 32. No streaming, compression, or
  missing-object negotiation: transfers include the full reachable history.
  This is appropriate for this small test, not large repositories. No hostile
  peer resource-exhaustion guarantee is claimed.
- macOS arm64 is tested. The network adapter also has a Linux executable-path
  implementation; FreeBSD remote serving is not supported by that lookup yet.
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
working copies, corrupt/incomplete objects, unsafe paths, oversized frames,
listener survival, restart, and interrupted checkout recovery.

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
