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

## Remaining costs

- Scans read/hash the complete included working tree every time.
- The scanner keeps file bodies in its results; Base allocation and JSON/tree
  intermediates further increase memory use. This is not a bounded-memory or
  streaming implementation, and large-repository memory scaling is unverified.
- Each new object is synchronized individually, favoring publication integrity
  over maximum commit throughput.
- History loads and verifies every ancestor. No pagination or history index.
- Objects live in a flat directory, without packing, sharding, or collection of
  unreachable objects/temp files.
- Diff displays complete contents and can produce large output.
- Linux/FreeBSD portability is source-level only; those hosts have not been run.

Run `python3 tests/bench.py` to reproduce timings on another machine. Do not
infer distributed, power-loss, or adversarial filesystem guarantees from these
small local benchmarks.
