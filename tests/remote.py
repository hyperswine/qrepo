#!/usr/bin/env python3

"""Native server/CLI regressions, using disposable roots and an ephemeral port."""

import concurrent.futures, base64, hashlib, json, os, pathlib, socket, struct, subprocess, tempfile, time

HOME = pathlib.Path(__file__).resolve().parents[1]
QR = os.environ.get('QR', str(HOME / 'qr'))


def cmd(root, *args, ok=True):
    p = subprocess.run([QR, '--root', str(root), *args],
                       capture_output=True,
                       text=True,
                       timeout=30)
    assert (p.returncode == 0) == ok, (args, p.returncode, p.stdout, p.stderr)
    return p.stdout + p.stderr


def head(root):
    return (root / '.qrepo/HEAD').read_text()


def frame(data):
    return struct.pack('!I', len(data)) + data


def rpc(port, body, objects=(), full=False):
    """Protocol 2: a header frame (JSON, saying how many objects follow), then
    each object raw.  Answers the reply's header, or (header, objects)."""
    body = dict(body, objects=len(objects))
    with socket.create_connection(('127.0.0.1', port), timeout=25) as s:
        s.sendall(
            frame(json.dumps(body).encode()) +
            b''.join(frame(o) for o in objects))

        def read(n):
            out = b''
            while len(out) < n:
                v = s.recv(n - len(out))
                if not v: raise ConnectionError('worker closed connection')
                out += v
            return out

        def one():
            return read(struct.unpack('!I', read(4))[0])

        header = json.loads(one())
        got = [one() for _ in range(header.get('objects', 0))]
        return (header, got) if full else header


def ready(port, proc):
    for _ in range(100):
        assert proc.poll() is None, 'server exited'
        try:
            if rpc(port, dict(version=2, op='head'))['ok']: return
        except (OSError, ConnectionError):
            pass
        time.sleep(.05)
    raise AssertionError('server not ready')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='qrepo-remote-test-') as d:
        root = pathlib.Path(d)
        server = root / 'server'
        a = root / 'alice'
        b = root / 'bob'
        for p in [server, a, b]:
            p.mkdir()
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        cmd(server, 'init')
        (server / 'state.json').write_text('{"alice":0,"bob":0}')
        (server / 'binary').write_bytes(bytes(range(256)))
        cmd(server, 'commit', 'seed')
        log = (root / 'server.log').open('w')
        proc = subprocess.Popen(
            [QR, '--root', str(server), 'serve',
             str(port)],
            stdout=log,
            stderr=log,
            start_new_session=True)
        try:
            ready(port, proc)
            cmd(a, 'clone', str(port))
            cmd(b, 'clone', str(port))
            assert head(a) == head(b) == head(server)
            assert (a / 'binary').read_bytes() == bytes(range(256))
            (a / 'alice.txt').write_text('alice')
            cmd(a, 'commit', 'alice')
            cmd(a, 'push')
            (b / 'bob.txt').write_text('bob')
            cmd(b, 'commit', 'bob')
            before = head(server)
            assert 'stale' in cmd(b, 'push', ok=False)
            assert head(server) == before
            cmd(b, 'pull')
            cmd(b, 'push')
            cmd(a, 'pull')
            assert head(a) == head(b) == head(server)
            assert (a / 'bob.txt').read_text() == 'bob' and (
                b / 'alice.txt').read_text() == 'alice'
            (a / 'state.json').write_text('{"alice":1,"bob":0}')
            cmd(a, 'commit', 'alice json')
            cmd(a, 'push')
            (b / 'state.json').write_text('{"alice":0,"bob":1}')
            cmd(b, 'commit', 'bob json')
            cmd(b, 'pull')
            cmd(b, 'push')
            cmd(a, 'pull')
            assert json.loads((a / 'state.json').read_text()) == {
                'alice': 1,
                'bob': 1
            }
            (a / 'alice.txt').unlink()
            cmd(a, 'commit', 'delete')
            cmd(a, 'push')
            cmd(b, 'pull')
            assert not (b / 'alice.txt').exists()
            (b / 'bob.txt').write_text('dirty')
            prior = head(b)
            cmd(b, 'pull', ok=False)
            assert head(b) == prior and (b / 'bob.txt').read_text() == 'dirty'
            cmd(b, 'restore', 'HEAD', 'bob.txt', '--force')
            # Same-field conflict preserves both committed history and working file.
            (a / 'state.json').write_text('{"alice":2,"bob":1}')
            cmd(a, 'commit', 'conflict a')
            cmd(a, 'push')
            (b / 'state.json').write_text('{"alice":3,"bob":1}')
            cmd(b, 'commit', 'conflict b')
            prior = head(b)
            assert 'conflict' in cmd(b, 'pull', ok=False)
            assert head(b) == prior and json.loads(
                (b / 'state.json').read_text())['alice'] == 3
            assert proc.poll() is None
            (b / 'state.json').write_text('{"alice":4,"bob":1}')
            cmd(b, 'commit-merge', 'manual resolution')
            cmd(b, 'push')
            cmd(a, 'pull')
            assert head(a) == head(b) == head(server) and json.loads(
                (a / 'state.json').read_text())['alice'] == 4
            print(
                'Clone/binary transfer, stale push, disjoint merge, JSON merge, deletion, dirty/conflict preservation: PASS',
                flush=True)
            # Two actual simultaneous push processes: exactly one CAS succeeds.
            (a / 'race-a').write_text('A')
            cmd(a, 'commit', 'race A')
            (b / 'race-b').write_text('B')
            cmd(b, 'commit', 'race B')

            def push_process(repo):
                return subprocess.run(
                    [QR, '--root', str(repo), 'push'],
                    capture_output=True,
                    timeout=30).returncode

            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                codes = list(pool.map(push_process, [a, b]))
            assert sorted(codes) == [0, 1], codes
            loser, winner = (a, b) if codes[0] else (b, a)
            cmd(loser, 'pull')
            cmd(loser, 'push')
            cmd(winner, 'pull')
            assert head(a) == head(b) == head(server)
            print(
                'Concurrent pushes: one winner, merged retry retains both changes: PASS',
                flush=True)
            fault = os.environ.get('QR_FAULT')
            if fault:
                (a / 'bob.txt').write_text('crash-safe remote update')
                cmd(a, 'commit', 'checkout crash')
                cmd(a, 'push')
                prior = head(b)
                p = subprocess.run(
                    [fault, '--root', str(b), 'pull'],
                    env=dict(os.environ, QREPO_FAULT='after:bob.txt'),
                    capture_output=True,
                    timeout=30)
                assert p.returncode == 86, (p.returncode, p.stdout, p.stderr)
                assert head(b) == prior and (b /
                                             '.qrepo/checkout.json').exists()
                cmd(b, 'recover')
                assert head(b) == head(server) and (
                    b / 'bob.txt').read_text() == 'crash-safe remote update'
                assert not (b / '.qrepo/checkout.json').exists()
                print('Interrupted multi-file checkout journal recovery: PASS',
                      flush=True)
            # Refusal and worker isolation: malformed or invalid packs never advance HEAD.
            before = head(server)
            answer = rpc(
                port,
                dict(version=2, op='push', expected='stale', head='0' * 64))
            assert not answer['ok']
            for objects in [[], [b'blob\nbad'],
                            [b'neither a blob nor a tree']]:
                try:
                    answer = rpc(
                        port,
                        dict(version=2,
                             op='push',
                             expected=before,
                             head='0' * 64), objects)
                except ConnectionError:
                    pass
                else:
                    assert not answer['ok']
                assert head(server) == before

            def packed(kind, body):
                raw = kind.encode() + b'\n' + body
                return hashlib.sha256(raw).hexdigest(), raw

            def refused(paths):
                bid, blob = packed('blob', b'escape')
                tid, tree = packed(
                    'tree',
                    json.dumps({
                        p: bid
                        for p in paths
                    }).encode())
                cid, commit = packed(
                    'commit',
                    json.dumps(
                        dict(root_state=tid,
                             parents=[before],
                             message='unsafe')).encode())
                try:
                    answer = rpc(
                        port,
                        dict(version=2, op='push', expected=before, head=cid),
                        [blob, tree, commit])
                except ConnectionError:
                    pass
                else:
                    assert not answer['ok'], paths
                assert head(server) == before, paths

            refused(['../outside'])
            assert not (root / 'outside').exists()
            # the metadata directory under another casing, and a publication temp name
            refused(['.QREPO/HEAD'])
            refused(['sub/.Qrepo/x'])
            refused(['.qr-tmp-1-1'])
            refused(['a', 'a/b'])
            # a header that promises objects which never arrive; a frame cut short
            with socket.create_connection(('127.0.0.1', port)) as s:
                s.sendall(
                    frame(
                        json.dumps(dict(version=2, op='head',
                                        objects=3)).encode()))
            with socket.create_connection(('127.0.0.1', port)) as s:
                s.sendall(struct.pack('!I', 1 << 30) + b'short')
            with socket.create_connection(('127.0.0.1', port)) as s:
                s.sendall(frame(b'not json'))
            assert rpc(port, dict(version=2, op='head'))['head'] == before
            assert not rpc(port, dict(version=999, op='head'))['ok']
            assert not rpc(port, dict(version=1, op='head'))['ok']
            # a fetch carries what the asker lacks: with everything, nothing; with nothing, all of it
            header, everything = rpc(port,
                                     dict(version=2, op='fetch', have=[]),
                                     full=True)
            header, nothing = rpc(port,
                                  dict(version=2, op='fetch', have=[before]),
                                  full=True)
            assert header['head'] == before and len(
                everything) > 10 and nothing == [], (len(everything),
                                                     len(nothing))
            assert all(
                hashlib.sha256(o).hexdigest() in os.listdir(server /
                                                            '.qrepo/objects')
                for o in everything)
            # what is not a commit, or not held, is no claim to have anything
            header, same = rpc(port,
                               dict(version=2,
                                    op='fetch',
                                    have=['f' * 64, 'not an id', 7]),
                               full=True)
            assert len(same) == len(everything)
            print(
                'Invalid packs, incomplete graphs, reserved paths, broken frames, negotiation, protocol version and listener survival: PASS',
                flush=True)
            proc.terminate()
            proc.wait(timeout=10)
            proc = subprocess.Popen(
                [QR, '--root', str(server), 'serve',
                 str(port)],
                stdout=log,
                stderr=log,
                start_new_session=True)
            ready(port, proc)
            assert rpc(port, dict(version=2, op='head'))['head'] == before
            print('Server restart preserves published history: PASS',
                  flush=True)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            log.close()
