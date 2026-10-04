# QRepo — native FP-RISC / Base / POSIX

A local checkpoint tool implemented in native FP-RISC. The original Sol
experiment is preserved separately in `../qrepo-sol`; it is not required to
build or run this version. This is still a prototype. A remote over HTTP, with
a bearer token, supports clone/fetch/pull/push and conservative merging, and is
meant to sit behind a TLS proxy for anything past a private network; see
[REMOTE.md](REMOTE.md). There is no automatic commit, background sync, or named
branch management.

## Build and use

Requires the FP-RISC compiler with 0-based string positions (fprisc from
2026-10-02 on; an earlier compiler counts from 1, and a build with it misreads
every object) and a C toolchain. `build.sh` uses `$FPR`, then `../fprisc/fpr`,
then `fpr` on PATH. macOS uses CommonCrypto; Linux/FreeBSD need
OpenSSL development headers/libraries (`libcrypto`). The remote's client
commands run `curl` (std has no TLS). Only macOS arm64 has been executed and
tested so far.

```sh
./build.sh
./qr --root /path/to/workspace init
./qr --root /path/to/workspace commit "first checkpoint"
./qr --root /path/to/workspace status
./qr --root /path/to/workspace tag v1
./qr --root /path/to/workspace history
./qr --root /path/to/workspace diff
./qr --root /path/to/workspace show v1 notes.txt
./qr --root /path/to/workspace restore v1 notes.txt
./qr --root /path/to/workspace restore v1 notes.txt --force
./qr --root /path/to/workspace ignore '.DS_Store'
./qr --root /path/to/workspace gc
./qr --root /path/to/workspace config device laptop
./qr --root /path/to/workspace config merge-rules on
./qr --root /path/to/workspace sync --json
./qr --root /path/to/workspace blame notes.txt
./qr --root /path/to/workspace conflicts
./qr --root /path/to/workspace merge-json base.json left.json right.json
```

Without `--root`, the root is the current directory; parent directories are
not searched automatically. `show` writes exact bytes, with no added newline.
`diff` is a complete before/after content display, not a line-oriented diff;
use it on text files. `merge-json` inputs are paths relative to the root.

Ignore rules, one per line of `.qrepoignore` (or added by `ignore`), are
patterns of path components. In a component, `*` matches any run of bytes and
`?` one byte; nothing else is special. A rule with no `/`, apart from a
trailing one, names a component at **any depth**: `.DS_Store`, `*.tmp`,
`build/`. A rule with a `/` inside, or a leading `/`, is **anchored at the
root**: `docs/draft.md`, `/notes.txt`, `docs/*.bak`. A rule that matches a
directory matches everything below it, and the scan does not descend into it.
Until 2026-10-04 a rule without `/` matched only at the root; such a rule
now matches at every depth (write `/name` for the old meaning). `.qrepo` is
always excluded; `.qrepoignore` is ordinary versioned content.

`gc` removes what nothing reaches: objects of refused or interrupted
transfers, superseded merges, and temporaries a dead writer left. Everything
HEAD, the remote head last seen, any tag or a pending checkout reaches is
first hashed whole, so a store with a missing or corrupt object refuses and
loses nothing. History is not pruned: every version a commit names is kept.

What QRepo does not version is **skipped and said**, not fatal: `status` lists
each symlink, device, FIFO and nested repository's `.qrepo` as
`skipped (not versioned): PATH`, and commits go ahead without them. A link is
never followed. Two kinds of name are reserved in every path component and
refused in any tree, local or remote: `.qrepo` under any casing, and
`.qr-tmp-*`, which publication gives its temporary files.

## Implementation boundary

- `qr.fpr`: command dispatch, snapshots, SHA-256 object identities, integrity
  checks, history, tags, ignores, guarded restores, and differences.
- `merge.fpr`: recursive JSON three-way merge and a caller-supplied validator.
  Arrays/scalars are atomic. Missing keys and JSON null are distinct.
- `text.fpr`: lines, a patience diff (unique-line anchors, then Myers' O(ND)
  on what has none, in an arena), a three-way line merge with optional rules
  for edits that only touch, and the line mapping blame follows. REMOTE.md
  says how merges decide.
- The remote is FP-RISC too: framing, the HTTP listener and the request
  workers are in `qr.fpr` over `std/tcp`, `std/stream` and `std/proc`. There
  is no network adapter in C. The client hands each request to `curl`, which
  does the TLS std does not have.
- `posix.c`: a host adapter for what Base does not have yet: descriptor-relative
  no-follow filesystem access, process locking, durable file publication,
  exact byte output, host SHA-256 (`std/digest` is correct and about 1,600
  times slower), and **streaming**: hashing a file where it lies, publishing
  one while hashing it again, checking out an object while verifying it, and
  framing objects into and out of transfer files, 64 KiB at a time. It refuses,
  by device and inode, any path that resolves into the metadata directory
  under another spelling. It does not implement repository/merge policy or
  invoke subprocesses.
- `qr`: a compiled executable. It does not invoke Sol, Python, or the compiler;
  its only subprocesses are its own request workers and, on a client, `curl`.
  Python is used only by tests/benchmarks and the experiment driver.

**Memory does not follow file contents.** FP-RISC reclaims its heap only at an
arena boundary or at exit, so a command that read the files it touched held
all of them at once: a 200 MB clone peaked near 1 GB. File bytes now stay in
`posix.c`, and per-file and per-commit work runs in an arena of its own; what
remains grows with the number of paths, not their size (PERFORMANCE.md).

The application declares `unsafe base.` and builds with `--system=posix`.
This permits unbounded operations and host effects; type and linearity checks
still run. It is not a claim of WCET, bounded memory, or formally proven safety.
No FP-RISC compiler/runtime changes are needed for this prototype.

## Format 2

Each immutable object is stored at `.qrepo/objects/<sha256>` as
`kind + "\n" + body`. The SHA-256 covers these exact bytes. A blob body is
arbitrary bytes; tree/commit bodies are UTF-8 JSON. Trees map root-relative
paths to blob IDs. Commits record tree, parents, local author, wall-clock
seconds, message, and metadata. `HEAD` contains a commit ID; `tags.json` maps
immutable names to IDs. Filenames must be UTF-8 and cannot contain reserved
`.qrepo`, empty, `.` or `..` components.

**Sol format 1 repositories are refused.** Their JSON object envelopes differ;
no in-place migration or backward reading is implemented. Use a fresh workspace
or preserve the old `.qrepo` before initializing this version. Do not point both
implementations at the same repository expecting interoperability.

Only bytes and paths are versioned. Permissions, executable bits, hardlink
identity, symlinks, and empty directories are not recorded. Replacing an
existing working file preserves its current permission bits; newly restored
files default to mode 0600, newly created directories to 0700.

## Safety and publication

Every command opens the repository root and holds an advisory exclusive
`command.lock` until exit. All subsequent filesystem paths are relative to
that descriptor. Ancestors are opened with `O_NOFOLLOW`; reads require regular
files and reject FIFOs/devices/symlinks. NUL and invalid UTF-8 paths are refused.
Errors are reported rather than interpreted as empty files. Reads compare file
size and modification/change timestamps before and after reading.

Updates write a unique temporary file in the destination directory, synchronize
it, publish it, and synchronize the directory. Immutable objects use exclusive
publication; existing objects are compared instead of overwritten. Mutable
refs use atomic replacement, never in-place truncation. All referenced objects
are published before `HEAD`. Initialization publishes `config.json` last.

A blob is hashed when it enters the store: a commit hashes each file where it
lies, then publishes it by copying it while hashing it again, so a file edited
in between publishes nothing and the commit says to retry. Imported objects
are hashed as they are stored, and a checkout hashes each object as it writes
it out. Commits and trees are hashed whenever they are read. The walk that
checks a history is complete therefore requires each blob only to be present
and a blob, instead of reading all of history again; `gc` hashes everything.

An interrupted commit can leave unreachable objects or `.qr-tmp-*` files, but
`HEAD` refers to a complete old or new checkpoint. A temporary file left in the
working tree is removed by the next command that scans, which holds the lock
its writer no longer does; it is never versioned. `gc` removes unreachable
objects. A synchronization error after publication can report failure even though
the new ref is visible; inspect history before retrying. `fsync` durability is
subject to the OS, filesystem, and device, and physical power-loss behavior has
not been tested. Remote/network filesystem semantics have not been validated.

Guarded restore refuses existing edits relative to HEAD unless `--force` is
provided; it recreates missing parent directories. Ordinary editors do not
honor QRepo's lock. A scan is not a filesystem-wide snapshot, and an external
write between the restore check and replacement can still be overwritten.
Concurrent hostile directory renames are outside the safety guarantee. Use a
quiescent workspace when an exact cross-file snapshot is required.

## Verification and performance

```sh
./test.sh
python3 tests/bench.py
```

`tests/fixes.py` holds one regression per fault found in the review of
2026-09-29: writes into `.qrepo` through another spelling, integers changed by
a JSON merge, scans stopped by a symlink, transfers past 16 MiB, temporary
files versioned, and directories and files trading places. `tests/text.fpr`
and `tests/textsync.py` cover the line diff and merge, every conflict kind,
`sync`'s states and blame; `tests/sync.py` covers the documents-sync work of
2026-10-04: tokens, ignore patterns,
collection, and 96 MiB through commit, status, clone and push with each
command under 32 MiB resident.

The tests use disposable repositories. They cover Unicode and binary bytes,
SHA-256 identity/deduplication, ancestry, immutable tags, status/diff, exact
show output, guarded/forced restore, missing-parent restoration, ignore pruning,
traversal, symlinks, special files, hardlink-safe replacement, concurrent
commands, corruption, JSON conflicts, and validator refusal. A separately built
test-only binary injects process death immediately before/after HEAD publication.
The release binary contains no enabled fault-injection environment switch.

See `PERFORMANCE.md` for local measurements and remaining scaling limits, and
`REMOTE.md` for the remote protocol, commands, recovery behavior, and experiment.
