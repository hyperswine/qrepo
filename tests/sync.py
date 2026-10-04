#!/usr/bin/env python3
"""Documents sync (2026-10-04): tokens, ignore patterns, collection, and the
streaming bound.  Disposable roots and ephemeral ports."""

import hashlib, json, os, pathlib, resource, socket, subprocess, sys, tempfile, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from remote import post, rpc, frame  # helpers only; remote.py's suite is guarded by __main__

HOME = pathlib.Path(__file__).resolve().parents[1]
QR = os.environ.get('QR', str(HOME / 'qr'))


def cmd(root, *args, ok=True, stdin=None):
    p = subprocess.run([QR, '--root', str(root), *args],
                       capture_output=True,
                       input=stdin,
                       timeout=120)
    out = (p.stdout + p.stderr).decode('utf8', 'replace')
    assert (p.returncode == 0) == ok, (args, p.returncode, out)
    return out


def head(root):
    return (root / '.qrepo/HEAD').read_text()


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Server:

    def __init__(self, root, *extra):
        self.port = free_port()
        self.proc = subprocess.Popen(
            [QR, '--root', str(root), 'serve', str(self.port), *extra],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE)
        for _ in range(100):
            assert self.proc.poll() is None, self.proc.stderr.read()
            try:
                socket.create_connection(('127.0.0.1', self.port), timeout=.2).close()
                return
            except OSError:
                time.sleep(.05)
        raise AssertionError('server not ready')

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.proc.terminate()
        self.proc.wait(timeout=10)


def peak_rss(root, *args):
    """The peak resident set of one qr command, in MiB."""
    probe = ('import resource, subprocess, sys\n'
             'p = subprocess.run(sys.argv[1:], capture_output=True)\n'
             'assert p.returncode == 0, p.stderr\n'
             'print(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)')
    out = subprocess.run([sys.executable, '-c', probe, QR, '--root', str(root), *args],
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    rss = int(out.stdout)
    return rss / (1 << 20) if sys.platform == 'darwin' else rss / 1024


with tempfile.TemporaryDirectory(prefix='qrepo-sync-test-') as d:
    base = pathlib.Path(d)

    def fresh(name):
        p = base / name
        p.mkdir()
        return p

    # Tokens.  Anything but loopback needs one; once one exists it is
    # required on loopback too; a client takes it on standard input.
    server = fresh('token-server')
    cmd(server, 'init')
    (server / 'a.txt').write_text('one')
    cmd(server, 'commit', 'seed')
    out = cmd(server, 'serve', str(free_port()), '--address', '0.0.0.0', ok=False)
    assert 'needs a token' in out, out
    cmd(server, 'serve-token')
    token = (server / '.qrepo/server-token').read_text().strip()
    assert len(token) == 64 and (server / '.qrepo/server-token').stat().st_mode & 0o077 == 0
    cmd(server, 'serve-token')  # a second call keeps the token it has
    assert (server / '.qrepo/server-token').read_text().strip() == token
    with Server(server) as s:
        assert post(s.port, b'')[0] == 401
        try:
            rpc(s.port, dict(version=3, op='head'))
            raise AssertionError('a request without a token was answered')
        except ConnectionError as e:
            assert 'HTTP 401' in str(e)
        assert rpc(s.port, dict(version=3, op='head'),
                   headers={'Authorization': 'Bearer ' + token})['head'] == head(server)
        assert post(s.port, b'', {'Authorization': 'Bearer ' + token[:-1]})[0] == 401
        assert post(s.port, b'', {'Authorization': token})[0] == 401
        a = fresh('token-a')
        out = cmd(a, 'clone', str(s.port), ok=False)
        assert 'HTTP 401' in out, out
        a = fresh('token-a2')
        cmd(a, 'clone', f'http://127.0.0.1:{s.port}/', '--token-stdin', stdin=token.encode() + b'\n')
        assert head(a) == head(server) and (a / 'a.txt').read_text() == 'one'
        assert (a / '.qrepo/remote-token').stat().st_mode & 0o077 == 0
        (a / 'a.txt').write_text('two')
        cmd(a, 'commit', 'edit')
        cmd(a, 'push')
        assert head(a) == head(server)
        cmd(a, 'remote', str(s.port), '--token-stdin', stdin=b'wrong')
        assert 'HTTP 401' in cmd(a, 'fetch', ok=False)
        assert 'not printable' in cmd(a, 'remote', str(s.port), '--token-stdin', stdin=b'a b\n', ok=False)
        assert 'printable ASCII' in cmd(a, 'remote', 'http://x y', ok=False)
        assert 'a remote is a port' in cmd(a, 'remote', 'ftp://example', ok=False)
        assert not list((a / '.qrepo/spool/client').iterdir())
    # a repository configured before URLs (protocol 2 wrote only a port)
    plain = fresh('plain')
    cmd(plain, 'init')
    (plain / 'p.txt').write_text('p')
    cmd(plain, 'commit', 'p')
    with Server(plain) as s:
        old = fresh('old-config')
        cmd(old, 'init')
        (old / '.qrepo/remote.json').write_text(json.dumps(dict(host='127.0.0.1', port=s.port)))
        cmd(old, 'pull')
        assert (old / 'p.txt').read_text() == 'p'
    print('Tokens: required off loopback, enforced once made, taken on stdin, never in arguments: PASS', flush=True)

    # Ignore patterns.
    r = fresh('ignore')
    cmd(r, 'init')
    files = ['.DS_Store', 'docs/.DS_Store', 'docs/deep/.DS_Store', 'a.tmp', 'docs/b.tmp',
             'notes.txt', 'docs/notes.txt', 'docs/x.bak', 'docs/deep/y.bak', 'x.bak',
             'build/out', 'docs/build/out', 'v1.md', 'v22.md', 'keep.md']
    for f in files:
        (r / f).parent.mkdir(parents=True, exist_ok=True)
        (r / f).write_text(f)
    for rule in ['.DS_Store', '*.tmp', '/notes.txt', 'docs/*.bak', 'build/', 'v?.md']:
        cmd(r, 'ignore', rule)
    for bad in ['../x', 'a//b', '.', '/', '']:
        assert 'ignore expects a pattern' in cmd(r, 'ignore', bad, ok=False), bad
    cmd(r, 'commit', 'patterns')
    status = cmd(r, 'status')
    assert status.startswith('clean'), status
    for f in files + ['.qrepoignore']:
        (r / f).unlink()
    out = cmd(r, 'status')
    deleted = {line[2:] for line in out.splitlines() if line.startswith('D ')}
    assert deleted == {'docs/notes.txt', 'docs/deep/y.bak', 'x.bak', 'v22.md', 'keep.md', '.qrepoignore'}, deleted
    print('Ignore patterns: names at any depth, * and ?, anchored paths, directories: PASS', flush=True)

    # Collection.
    r = fresh('gc')
    cmd(r, 'init')
    (r / 'f.txt').write_text('v1')
    cmd(r, 'commit', 'v1')
    cmd(r, 'tag', 'first')
    (r / 'f.txt').write_text('v2')
    cmd(r, 'commit', 'v2')
    objects = r / '.qrepo/objects'
    stray = b'blob\nnothing names me'
    (objects / hashlib.sha256(stray).hexdigest()).write_bytes(stray)
    (objects / '.qr-tmp-1-1').write_bytes(b'remains')
    before = set(os.listdir(objects))
    out = cmd(r, 'gc')
    assert 'removed 2 unreachable objects' in out, out
    assert set(os.listdir(objects)) == before - {hashlib.sha256(stray).hexdigest(), '.qr-tmp-1-1'}
    assert cmd(r, 'show', 'first', 'f.txt') == 'v1' and cmd(r, 'show', 'HEAD', 'f.txt') == 'v2'
    # a reachable object that is corrupt stops collection before anything goes
    (objects / hashlib.sha256(stray).hexdigest()).write_bytes(stray)
    v1 = hashlib.sha256(b'blob\nv1').hexdigest()
    (objects / v1).write_bytes(b'blob\nV1')
    before = set(os.listdir(objects))
    assert 'integrity failure' in cmd(r, 'gc', ok=False)
    assert set(os.listdir(objects)) == before
    # and a corrupt blob prints nothing
    p = subprocess.run([QR, '--root', str(r), 'show', 'first', 'f.txt'], capture_output=True)
    assert p.returncode != 0 and p.stdout == b'', p
    print('Collection: unreachable objects and dead temps go, a corrupt store loses nothing: PASS', flush=True)

    # The streaming bound: memory does not follow the bytes.  96 MiB in
    # three files, through commit, status, clone and push.
    server = fresh('big-server')
    cmd(server, 'init')
    for i in range(3):
        (server / f'big{i}.bin').write_bytes(os.urandom(32 << 20))
    assert peak_rss(server, 'commit', 'big') < 32
    assert peak_rss(server, 'status') < 32
    with Server(server) as s:
        a = fresh('big-a')
        assert peak_rss(a, 'clone', str(s.port)) < 32
        for i in range(3):
            assert (a / f'big{i}.bin').read_bytes() == (server / f'big{i}.bin').read_bytes()
        (a / 'big3.bin').write_bytes(os.urandom(32 << 20))
        cmd(a, 'commit', 'more')
        assert peak_rss(a, 'push') < 32
        assert head(a) == head(server)
    print('Streaming: 96 MiB through commit, status, clone and push, each under 32 MiB: PASS', flush=True)
