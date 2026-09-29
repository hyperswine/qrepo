#!/usr/bin/env python3

"""Measure warm native commands on fresh disposable repositories."""

import json, os, pathlib, statistics, subprocess, tempfile, time

QR = os.environ.get('QR',
                    str(pathlib.Path(__file__).resolve().parents[1] / 'qr'))


def run(root, *args):
    start = time.perf_counter()
    p = subprocess.run([QR, '--root', str(root), *args],
                       capture_output=True,
                       timeout=60)
    if p.returncode: raise RuntimeError((args, p.stdout, p.stderr))
    return time.perf_counter() - start


results = []
for count in [1, 100, 1000]:
    with tempfile.TemporaryDirectory(prefix='qrepo-native-bench-') as d:
        root = pathlib.Path(d)
        init = run(root, 'init')
        for i in range(count):
            (root / f'{i:05}.txt').write_bytes(
                (f'file {i}\n'.encode() * 600)[:4096])
        commit = run(root, 'commit', 'benchmark')
        status = [run(root, 'status') for _ in range(5)]
        results.append(
            dict(files=count,
                 bytes_per_file=4096,
                 init_s=init,
                 commit_s=commit,
                 status_median_s=statistics.median(status),
                 status_samples_s=status))
print(json.dumps(results, indent=2))
