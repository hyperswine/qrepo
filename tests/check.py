#!/usr/bin/env python3

"""Disposable-repository integration tests; no changes to real workspaces."""

import concurrent.futures, errno, fcntl, hashlib, json, os, pathlib, subprocess, tempfile, time

HOME = pathlib.Path(__file__).resolve().parents[1]
QR = os.environ.get('QR', str(HOME / 'qr'))


def command(root, *args, ok=True, raw=False):
    p = subprocess.run([QR, '--root', str(root), *args],
                       capture_output=True,
                       timeout=30)
    if (p.returncode == 0) != ok:
        raise AssertionError(
            f'{args}: exit {p.returncode}\n{p.stdout!r}\n{p.stderr!r}')
    return p.stdout if raw else (p.stdout + p.stderr).decode('utf8', 'replace')


def object_(root, oid, kind):
    raw = (root / '.qrepo/objects' / oid).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == oid
    assert raw.startswith(kind.encode() + b'\n')
    return raw[len(kind) + 1:]


def head(root):
    return (root / '.qrepo/HEAD').read_text()


def record(root, oid):
    return json.loads(object_(root, oid, 'commit'))


def tree(root, oid):
    return json.loads(object_(root, record(root, oid)['root_state'], 'tree'))


with tempfile.TemporaryDirectory(prefix='qrepo-native-test-') as d:
    root = pathlib.Path(d)
    command(root, 'init')
    command(root, 'init', ok=False)
    contents = 'hello café 🌱\n'.encode()
    (root / 'a.txt').write_bytes(contents)
    (root / 'same.txt').write_bytes(contents)
    (root / 'sub').mkdir()
    (root / 'sub/x.txt').write_text('nested\n')
    binary = bytes(range(256)) * 3
    (root / 'binary').write_bytes(binary)
    command(root, 'commit', 'first')
    first = head(root)
    t = tree(root, first)
    assert t['a.txt'] == t['same.txt']
    assert object_(root, t['a.txt'], 'blob') == contents
    assert object_(root, t['binary'], 'blob') == binary
    assert command(root, 'show', 'HEAD', 'binary', raw=True) == binary
    assert command(root, 'show', 'HEAD', 'a.txt', raw=True) == contents
    assert record(root, first)['parents'] == []
    assert 'clean' in command(root, 'status')
    command(root, 'tag', 'v1')
    command(root, 'tag', 'v1', ok=False)
    (root / 'a.txt').write_text('updated\n')
    (root / 'same.txt').unlink()
    s = command(root, 'status')
    assert 'M a.txt' in s and 'D same.txt' in s, s
    assert 'updated' in command(root, 'diff')
    command(root, 'restore', 'v1', 'a.txt', ok=False)
    command(root, 'restore', 'v1', 'a.txt', '--force')
    assert (root / 'a.txt').read_bytes() == contents
    command(root, 'restore', 'v1', '../outside', '--force', ok=False)
    command(root, 'commit', 'remove duplicate')
    second = head(root)
    assert record(root, second)['parents'] == [first]
    s = command(root, 'history')
    assert first in s and second in s
    command(root, 'commit', 'unchanged', ok=False)
    assert head(root) == second
    (root / 'binary').write_bytes(b'changed')
    command(root, 'restore', 'v1', 'binary', '--force')
    assert (root / 'binary').read_bytes() == binary
    (root / 'ignored').mkdir()
    (root / 'ignored/data').write_text('skip')
    command(root, 'ignore', 'ignored/')
    command(root, 'commit', 'ignore directory')
    assert 'ignored/data' not in tree(root, head(root))
    (root / 'ignored/loop').symlink_to(
        root)  # ignored directories must actually be pruned
    assert 'clean' in command(root, 'status')
    print(
        'Snapshots, binary/Unicode, deduplication, tags, history, restore and pruning: PASS',
        flush=True)
    # What is not versioned is skipped and said; it does not stop the scan, and
    # a link is never followed into what it names.
    (root / 'escape').symlink_to('/tmp')
    s = command(root, 'status')
    assert 'skipped (not versioned): escape' in s and 'clean' in s, s
    (root / 'escape').unlink()
    os.mkfifo(root / 'pipe')
    s = command(root, 'status')
    assert 'skipped (not versioned): pipe' in s and 'clean' in s, s
    (root / 'pipe').unlink()
    # Parent traversal/symlink refusal during a forced restore.
    (root / 'sub/x.txt').unlink()
    (root / 'sub').rmdir()
    with tempfile.TemporaryDirectory(prefix='qrepo-outside-') as out:
        outside = pathlib.Path(out)
        (outside / 'x.txt').write_text('untouched')
        (root / 'sub').symlink_to(outside)
        command(root, 'restore', 'v1', 'sub/x.txt', '--force', ok=False)
        assert (outside / 'x.txt').read_text() == 'untouched'
        (root / 'sub').unlink()
    command(root, 'restore', 'v1', 'sub/x.txt', '--force')
    assert (root / 'sub/x.txt').read_text() == 'nested\n'
    # Ref publication replaces an inode, never truncates a hardlinked outside file.
    with tempfile.TemporaryDirectory(prefix='qrepo-outside-') as out:
        outside = pathlib.Path(out) / 'ref'
        old = head(root)
        outside.write_text(old)
        (root / '.qrepo/HEAD').unlink()
        os.link(outside, root / '.qrepo/HEAD')
        (root / 'new').write_text('new')
        command(root, 'commit', 'hardlink safety')
        assert outside.read_text() == old and head(root) != old
    # QRepo processes serialize: one winner, one unchanged refusal.
    (root / 'new').write_text('race')

    def commit(i):
        return subprocess.run(
            [QR, '--root',
             str(root), 'commit', f'concurrent {i}'],
            capture_output=True,
            timeout=30).returncode

    with concurrent.futures.ThreadPoolExecutor(2) as ex:
        codes = list(ex.map(commit, [1, 2]))
    assert sorted(codes) == [0, 1], codes
    # Another holder of command.lock must block all repository access.
    with (root / '.qrepo/command.lock').open('r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        p = subprocess.Popen([QR, '--root', str(root), 'status'],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        time.sleep(.15)
        assert p.poll() is None
        fcntl.flock(lock, fcntl.LOCK_UN)
        out, err = p.communicate(timeout=10)
        assert p.returncode == 0, (out, err)
    odd = os.fsencode(root) + b'/invalid-\xff'
    try:
        fd = os.open(odd, os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError as e:
        if e.errno != errno.EILSEQ: raise
        print('Host filesystem already refuses invalid UTF-8 filenames',
              flush=True)
    else:
        os.close(fd)
        command(root, 'status', ok=False)
        os.unlink(odd)
    current = head(root)
    path = root / '.qrepo/objects' / current
    path.write_bytes(path.read_bytes() + b' ')
    command(root, 'history', ok=False)
    command(root, 'commit', 'corrupt', ok=False)
    assert head(root) == current
    print(
        'Symlinks, special files, hardlinks, traversal, locking, concurrency, integrity: PASS',
        flush=True)
with tempfile.TemporaryDirectory(prefix='qrepo-merge-test-') as d:
    root = pathlib.Path(d)
    command(root, 'init')

    def merge(b, l, r, expected=None, ok=True):
        for name, value in zip(['b', 'l', 'r'], [b, l, r]):
            (root / name).write_text(json.dumps(value))
        out = command(root, 'merge-json', 'b', 'l', 'r', ok=ok)
        if ok: assert json.loads(out) == expected, (out, expected)

    base = {'a': 1, 'b': 1}
    left = {'a': 2, 'b': 1}
    right = {'a': 1, 'b': 2}
    merge(base, left, right, {'a': 2, 'b': 2})
    merge(base, left, {'a': 3, 'b': 1}, ok=False)
    merge(base, {'b': 1}, left, ok=False)
    merge({}, {'x': None}, {}, {'x': None})
    merge(base, left, left, left)
    merge([], [1], [2], ok=False)
    print('Recursive JSON merge, conflicts, null/absence, atomic arrays: PASS',
          flush=True)
print('Native QRepo integration: PASS', flush=True)
FAULT_QR = os.environ.get('QR_FAULT')
if FAULT_QR:
    for stage in ['before', 'after']:
        with tempfile.TemporaryDirectory(prefix='qrepo-crash-test-') as d:
            root = pathlib.Path(d)
            command(root, 'init')
            (root / 'a').write_text('old')
            command(root, 'commit', 'old')
            old = head(root)
            (root / 'a').write_text('new')
            p = subprocess.run(
                [FAULT_QR, '--root',
                 str(root), 'commit', 'new'],
                env=dict(os.environ, QREPO_FAULT=f'{stage}:.qrepo/HEAD'),
                capture_output=True,
                timeout=30)
            assert p.returncode == 86, (p.returncode, p.stdout, p.stderr)
            new = head(root)
            assert (new == old) == (stage == 'before')
            t = tree(root, new)
            assert object_(root, t['a'],
                           'blob') == (b'old' if stage == 'before' else b'new')
            command(root, 'history')
            if stage == 'before': command(root, 'commit', 'retry')
    print('Process death immediately before/after HEAD publication: PASS',
          flush=True)
else:
    print('Crash injection skipped (set QR_FAULT to the test-only binary)',
          flush=True)
