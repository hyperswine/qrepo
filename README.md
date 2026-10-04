# QRepo — native FP-RISC / Base / POSIX

A local checkpoint tool implemented in native FP-RISC. The original Sol
experiment is preserved separately in `../qrepo-sol`; it is not required to
build or run this version. This is still a prototype. An experimental loopback remote now supports
clone/fetch/pull/push and conservative merging; see [REMOTE.md](REMOTE.md).
There is no Internet hosting, automatic commit, or named branch management.

## Build and use

Requires the FP-RISC compiler with 0-based string positions (fprisc from
2026-10-02 on; an earlier compiler counts from 1, and a build with it misreads
every object) and a C toolchain. `build.sh` uses `$FPR`, then `../fprisc/fpr`,
then `fpr` on PATH. macOS uses CommonCrypto; Linux/FreeBSD need
OpenSSL development headers/libraries (`libcrypto`). Only macOS arm64 has been
executed and tested so far.

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
./qr --root /path/to/workspace ignore 'build/'
./qr --root /path/to/workspace merge-json base.json left.json right.json
```

Without `--root`, the root is the current directory; parent directories are
not searched automatically. `show` writes exact bytes, with no added newline.
`diff` is a complete before/after content display, not a line-oriented diff;
use it on text files. `merge-json` inputs are paths relative to the root.
Ignore rules are literal root-relative file paths or directory prefixes ending
in `/`, not globs. Ignored directories are pruned before descent. `.qrepo` is
always excluded; `.qrepoignore` is ordinary versioned content.

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
- The remote is FP-RISC too: framing, the listener and the request workers are
  in `qr.fpr` over `std/tcp`, `std/stream` and `std/proc`. There is no network
  adapter in C (there was, `network.c`, until 2026-09-29).
- `posix.c`: a host adapter for what Base does not have yet: descriptor-relative
  no-follow filesystem access, process locking, durable file publication,
  exact byte output, and host SHA-256 (`std/digest` is correct and about 1,600
  times slower). It refuses, by device and inode, any path that resolves into
  the metadata directory under another spelling. It does not implement
  repository/merge policy or invoke subprocesses.
- `qr`: a compiled executable. It does not invoke Sol, Python, or the compiler.
  Python is used only by tests/benchmarks and the experiment driver.

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

An interrupted commit can leave unreachable objects or `.qr-tmp-*` files, but
`HEAD` refers to a complete old or new checkpoint. A temporary file left in the
working tree is removed by the next command that scans, which holds the lock
its writer no longer does; it is never versioned. There is no garbage collector
for objects yet. A synchronization error after publication can report failure even though
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
files versioned, and directories and files trading places.

The tests use disposable repositories. They cover Unicode and binary bytes,
SHA-256 identity/deduplication, ancestry, immutable tags, status/diff, exact
show output, guarded/forced restore, missing-parent restoration, ignore pruning,
traversal, symlinks, special files, hardlink-safe replacement, concurrent
commands, corruption, JSON conflicts, and validator refusal. A separately built
test-only binary injects process death immediately before/after HEAD publication.
The release binary contains no enabled fault-injection environment switch.

See `PERFORMANCE.md` for local measurements and remaining scaling limits, and
`REMOTE.md` for the remote protocol, commands, recovery behavior, and experiment.
