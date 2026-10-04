# Native QRepo performance checkpoint

Measured on the local macOS arm64 host, 2026-09-28. These are subprocess
wall-clock measurements on warm local storage, not cold-disk or network tests.
Each status number is the median of five new process invocations. Each file
contains 4 KiB; content differs by file. Every status still reads and hashes
all included files: no optimistic stat cache is hiding work.

| Files | First commit | Clean status |
|---:|---:|---:|
| 1 | 5.4 ms | 3.8 ms |
| 100 | 41 ms | 5.8 ms |
| 1,000 | 386 ms | 25 ms |

The earlier Sol one-file status measurement was 11.56 seconds. That was a
separate earlier measurement with a different tiny fixture, not a simultaneous
controlled comparison. The native executable avoids per-command compilation
and hashing subprocesses. Native path unions and diff lookups now use balanced
sets/maps instead of repeated list searches. SHA-256 uses CommonCrypto on macOS
and OpenSSL on other supported POSIX hosts.

## Compiler issue encountered

The original large nested `case` command dispatcher caused the native compiler
to consume minutes and approximately 20.6 GB physical footprint before it was
stopped. Removing the FP-RISC digest import alone did not resolve the stall.
Rewriting command dispatch as ordinary function clauses brought the cached-unit
build to approximately one second. A small JSON-only program also built normally.

Source inspection shows that function-clause lowering shares fallthrough using
join functions, whereas `compileArms` repeats the fallback in nested pattern
tests. QRepo uses function clauses to avoid that expansion. The compiler itself
has not been patched, and the earlier Sol slowdown has not been remeasured with
this rewrite. That is a separate, useful compiler follow-up.

## Memory, 2026-10-04

Same host, warm storage, one command each, peak resident set from
`/usr/bin/time -l`. "Before" is the build of 2026-10-04 before streaming;
clones are over loopback HTTP.

| 200 files x 1 MiB (200 MB) | Before | After |
|---|---:|---:|
| First commit | 614 MB | 4 MB |
| Clean status | 411 MB | 4 MB |
| Clone | 1,053 MB | 5 MB |

| 2,000 files x 2 KiB in 20 directories | Before | After |
|---|---:|---:|
| Clone | 189 MB | 30 MB |
| `gc` (hashes every reachable object) | 54 MB | 10 MB |

The listener stayed at 3 MB throughout. A 400 MB clone took 2.6 s and a
200 MB push 1.6 s. `tests/sync.py` keeps 96 MiB through commit, status, clone
and push under 32 MiB each.

Why it was high: FP-RISC frees a heap only at an arena boundary or at exit,
so every file body read, and every small per-file allocation, stayed until
the command ended; and the history walk re-read every blob of the whole
history, up to three times per pull. Now file bytes stay in `posix.c`, each
file and each commit is processed in an arena of its own, and a blob is
hashed once, when it enters the store (README, Safety). What remains grows
with the number of paths and commits (trees, sets of identities), not with
the bytes.

## Text diff and merge, 2026-10-04

A 5,000-line file with 8 scattered edits on each of two devices, and a
2,000-line CSV one device rewrote entirely while the other shuffled it:

| | Myers alone | Patience + Myers |
|---|---:|---:|
| `status --json` | 8.1 s, 2,579 MB | 0.02 s, 28 MB |
| `sync` merging both | 28.2 s, 7,145 MB | 0.08 s, 52 MB |

Same results: the 16 edits merge, the CSV is one conflict. Myers' trace is
O(D^2) in the lines that differ, and a rewrite makes D the whole file; the
patience step leaves Myers only the gaps without unique lines, minus lines
the other side never has. `blame` of that 5,000-line file over 200 commits,
each changing it: 1.1 s, 153 MB (each step's diff in an arena; the line
numbers carried down grow with lines times depth).

## Remaining costs

- Scans read/hash the complete included working tree every time.
- Trees are JSON parsed whole, and a command holds the sets of paths and
  identities it works on: memory grows with the number of files and commits
  (about 15 KB per path in a clone), not their size. Repositories of 100,000
  paths are unmeasured.
- Each new object is synchronized individually, favoring publication integrity
  over maximum commit throughput.
- History is walked whole (each commit's tree is parsed once per walk); no
  pagination or history index. Old versions are kept for as long as a commit
  names them: `gc` removes only what nothing reaches.
- Objects live in a flat directory, without packing or sharding.
- Diff displays complete contents and can produce large output.
- Linux/FreeBSD portability is source-level only; those hosts have not been run.

Run `python3 tests/bench.py` to reproduce timings on another machine. Do not
infer distributed, power-loss, or adversarial filesystem guarantees from these
small local benchmarks.
