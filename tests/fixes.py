#!/usr/bin/env python3
"""One regression per fault found in review (2026-09-29).  Disposable roots."""

import hashlib, json, os, pathlib, socket, struct, subprocess, tempfile, time

HOME = pathlib.Path(__file__).resolve().parents[1]

QR = os.environ.get('QR', str(HOME / 'qr'))


def cmd(root, *args, ok=True, exe=QR, env=None):
    p = subprocess.run([exe, '--root', str(root), *args],
                       capture_output=True,
                       text=True,
                       timeout=120,
                       env=dict(os.environ, **(env or {})))
    assert (p.returncode == 0) == ok, (args, p.returncode, p.stdout, p.stderr)
    return p.stdout + p.stderr


def head(root):
    return (root / '.qrepo/HEAD').read_text()


def put(root, kind, body):
    raw = kind.encode() + b'\n' + body
    oid = hashlib.sha256(raw).hexdigest()
    (root / '.qrepo/objects' / oid).write_bytes(raw)
    return oid


class Server:

    def __init__(self, root):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            self.port = s.getsockname()[1]
        self.proc = subprocess.Popen(
            [QR, '--root', str(root), 'serve',
             str(self.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                socket.create_connection(('127.0.0.1', self.port),
                                         timeout=.2).close()
                break
            except OSError:
                time.sleep(.05)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.proc.terminate()
        self.proc.wait(timeout=10)


class Hostile:
    """A peer that speaks protocol 2 and answers every request with these
    objects and this head, whatever they are: an honest server refuses to
    send a tree it would not accept, so the client's own refusal needs a liar."""

    def __init__(self, head, objects):
        import threading
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        reply = frame(
            json.dumps(
                dict(version=2, ok=True, head=head,
                     objects=len(objects))).encode()) + b''.join(
                         frame(o) for o in objects)

        def run():
            while True:
                try:
                    conn, _ = self.sock.accept()
                except OSError:
                    return
                with conn:
                    try:
                        conn.settimeout(5)
                        n = struct.unpack('!I', conn.recv(4))[0]
                        conn.recv(n)
                        conn.sendall(reply)
                    except OSError:
                        pass

        threading.Thread(target=run, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.sock.close()


def frame(data):
    return struct.pack('!I', len(data)) + data


def raw(kind, body):
    return kind.encode() + b'\n' + body


def oid(data):
    return hashlib.sha256(data).hexdigest()


CASEFOLDS = None
with tempfile.TemporaryDirectory(prefix='qrepo-fixes-') as d:
    base = pathlib.Path(d)

    def fresh(name):
        p = base / name
        p.mkdir()
        return p

    probe = fresh('case')
    (probe / 'x').write_text('')
    CASEFOLDS = (probe / 'X').exists()

    # 1, 2. A remote tree may not name the metadata directory, under any spelling;
    # nothing is planted in .qrepo, and the clone is not left wedged.
    for hostile in [
            '.QREPO/planted.txt', '.QREPO/checkout.json', '.Qrepo/HEAD',
            'sub/.qrepo/x', '.qr-tmp-9-9'
    ]:
        blob = raw('blob', b'planted by the remote')
        tree = raw(
            'tree',
            json.dumps({
                hostile: oid(blob),
                'ok.txt': oid(blob)
            }).encode())
        commit = raw(
            'commit',
            json.dumps(
                dict(root_state=oid(tree),
                     parents=[],
                     author='local',
                     timestamp=1,
                     message='hostile',
                     metadata={})).encode())
        victim = fresh('v' + hashlib.sha1(hostile.encode()).hexdigest()[:6])
        with Hostile(oid(commit), [blob, tree, commit]) as s:
            out = cmd(victim, 'clone', str(s.port), ok=False)
        assert 'unsafe remote path' in out, (hostile, out)
        # an honest server does not send such a tree either
        server = fresh('s' + hashlib.sha1(hostile.encode()).hexdigest()[:6])
        cmd(server, 'init')
        for o in [blob, tree, commit]:
            (server / '.qrepo/objects' / oid(o)).write_bytes(o)
        (server / '.qrepo/HEAD').write_text(oid(commit))
        with Server(server) as s:
            cmd(fresh('w' + hashlib.sha1(hostile.encode()).hexdigest()[:6]),
                'clone',
                str(s.port),
                ok=False)
        meta = sorted(p.name for p in (victim / '.qrepo').iterdir())
        assert 'planted.txt' not in meta and 'checkout.json' not in meta, (
            hostile, meta)
        assert head(victim) == '' and not (victim / 'ok.txt').exists(), hostile
        assert 'clean' in cmd(
            victim, 'status'), hostile  # not wedged: commands still run
    # ...and the host adapter refuses by identity, whatever the policy above it says
    root = fresh('identity')
    cmd(root, 'init')
    (root / 'a.txt').write_text('1')
    cmd(root, 'commit', 'one')
    if CASEFOLDS:
        out = cmd(root, 'restore', 'HEAD', '.QREPO/HEAD', ok=False)
        assert head(root) != '', out
        out = cmd(root, 'show', 'HEAD', '.QREPO/HEAD', ok=False)
    print(
        '1, 2. A remote cannot write into .qrepo under any spelling, nor wedge a clone%s: PASS'
        % ('' if CASEFOLDS else
           ' (volume is case-sensitive: identity check not exercised)'),
        flush=True)

    # 3. A JSON merge returns the numbers it was given.
    root = fresh('numbers')
    cmd(root, 'init')
    doc = lambda a, b: json.dumps(
        dict(big=12345678901234567890,
             most=4611686018427387903,
             more=4611686018427387904,
             neg=-98765432109876543210,
             pi=3.141592653589793,
             tiny=1e-7,
             a=a,
             b=b))
    (root / 'base.json').write_text(doc(1, 1))
    (root / 'left.json').write_text(doc(2, 1))
    (root / 'right.json').write_text(doc(1, 2))
    merged = json.loads(
        cmd(root, 'merge-json', 'base.json', 'left.json', 'right.json'))
    assert merged == json.loads(doc(2, 2)), merged
    print('3. A JSON merge keeps integers past 62 bits exactly: PASS',
          flush=True)

    # 4. Symlinks, special files and a nested repository are skipped and said.
    root = fresh('scan')
    cmd(root, 'init')
    (root / 'a.txt').write_text('1')
    (root / 'link').symlink_to('a.txt')
    (root / 'dangling').symlink_to('nowhere')
    os.mkfifo(root / 'pipe')
    (root / 'vendor/lib/.qrepo').mkdir(parents=True)
    (root / 'vendor/lib/.qrepo/HEAD').write_text('x')
    (root / 'vendor/lib/code.txt').write_text('kept')
    out = cmd(root, 'commit', 'with things that are not versioned')
    tree = json.loads((root / '.qrepo/objects' / json.loads(
        (root / '.qrepo/objects' / head(root)).read_bytes().split(
            b'\n', 1)[1])['root_state']).read_bytes().split(b'\n', 1)[1])
    assert sorted(tree) == ['a.txt', 'vendor/lib/code.txt'], tree
    out = cmd(root, 'status')
    for name in ['link', 'dangling', 'pipe', 'vendor/lib/.qrepo']:
        assert 'skipped (not versioned): ' + name + ' ' in out, (name, out)
    assert 'clean' in out, out
    print(
        '4. Symlinks, special files and nested metadata are skipped, reported, never followed: PASS',
        flush=True)

    # 5. History past 16 MiB, and one object past it, still clone, fetch and push.
    server = fresh('big-server')
    cmd(server, 'init')
    big = os.urandom(20 * 1024 * 1024)
    (server / 'big.bin').write_bytes(big)
    cmd(server, 'commit', '20 MiB')
    a = fresh('big-a')
    b = fresh('big-b')
    with Server(server) as s:
        cmd(a, 'clone', str(s.port))
        assert (a / 'big.bin').read_bytes() == big
        more = os.urandom(5 * 1024 * 1024)
        (a / 'more.bin').write_bytes(more)
        cmd(a, 'commit', '5 MiB more')
        cmd(a, 'push')
        cmd(b, 'clone', str(s.port))
        assert (b / 'more.bin').read_bytes() == more and (
            b / 'big.bin').read_bytes() == big
        assert head(a) == head(b) == head(server)
    print('5. A 20 MiB object and a 25 MiB history clone and push: PASS',
          flush=True)

    # 6. What a process that died mid-publication left in the working tree is removed, not versioned.
    fault = os.environ.get('QR_FAULT')
    if fault:
        root = fresh('temp')
        cmd(root, 'init')
        (root / 'sub').mkdir()
        (root / 'sub/notes.txt').write_text('v1')
        cmd(root, 'commit', 'one')
        (root / 'sub/notes.txt').write_text('v2')
        cmd(root, 'commit', 'two')
        p = subprocess.run([
            fault, '--root',
            str(root), 'restore', 'HEAD', 'sub/notes.txt', '--force'
        ],
                           env=dict(os.environ,
                                    QREPO_FAULT='before:sub/notes.txt'),
                           capture_output=True,
                           timeout=30)
        assert p.returncode == 86
        left = [
            n for n in os.listdir(root / 'sub') if n.startswith('.qr-tmp-')
        ]
        assert len(left) == 1, left
        (root /
         '.qr-tmp-mine').write_text('a file of the user\'s, not ours'
                                    )  # not the pattern: kept, and skipped
        out = cmd(root, 'status')
        assert 'clean' in out and 'A ' not in out, out
        assert not [
            n for n in os.listdir(root / 'sub') if n.startswith('.qr-tmp-')
        ] and (root / '.qr-tmp-mine').exists()
        assert 'skipped (not versioned): .qr-tmp-mine' in out, out
        print(
            '6. A dead process\'s publication temp file is removed, never versioned: PASS',
            flush=True)

    # 7. A directory becomes a file, and a file a directory, across a pull.
    server = fresh('shape-server')
    cmd(server, 'init')
    a = fresh('shape-a')
    b = fresh('shape-b')
    with Server(server) as s:
        cmd(a, 'clone', str(s.port))
        (a / 'a/deep').mkdir(parents=True)
        (a / 'a/deep/b.txt').write_text('in a directory')
        (a / 'f').write_text('a file')
        cmd(a, 'commit', 'shapes')
        cmd(a, 'push')
        cmd(b, 'clone', str(s.port))
        import shutil
        shutil.rmtree(a / 'a')
        (a / 'a').write_text('now a file')
        (a / 'f').unlink()
        (a / 'f/g').parent.mkdir()
        (a / 'f/g').write_text('now in a directory')
        cmd(a, 'commit', 'shapes changed')
        cmd(a, 'push')
        cmd(b, 'pull')
        assert (b / 'a').read_text() == 'now a file' and (
            b /
            'f/g').read_text() == 'now in a directory' and head(b) == head(a)
        assert 'clean' in cmd(b, 'status')
        # a directory that holds work which is not in the checkpoint is not replaced, and the path is named
        shutil.rmtree(a / 'f')
        (a / 'f').write_text('a file again')
        cmd(a, 'commit', 'back')
        cmd(a, 'push')
        (b / 'f/untracked.txt').write_text('mine')
        cmd(b, 'ignore', 'f/untracked.txt')
        cmd(b, 'commit', 'ignore rule')
        prior = head(b)
        out = cmd(b, 'pull', ok=False)
        assert 'f/untracked.txt' in out and (b / 'f/untracked.txt').read_text(
        ) == 'mine' and (b / 'f/g').exists(), out
        assert not (b / '.qrepo/checkout.json').exists() and 'clean' in cmd(
            b, 'status')
        # deleting the last file of a directory takes the directory with it
        (a / 'gone/deep').mkdir(parents=True)
        (a / 'gone/deep/x').write_text('x')
        cmd(a, 'commit', 'add gone')
        cmd(a, 'push')
        c = fresh('shape-c')
        cmd(c, 'clone', str(s.port))
        assert (c / 'gone/deep/x').exists()
        shutil.rmtree(a / 'gone')
        cmd(a, 'commit', 'remove gone')
        cmd(a, 'push')
        cmd(c, 'pull')
        assert not (c / 'gone').exists()
    print(
        '7. Directory and file trade places across a pull; untracked work under one is refused by name: PASS',
        flush=True)
