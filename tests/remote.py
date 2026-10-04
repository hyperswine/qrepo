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


def post(port, payload, headers=None, path='/qrepo', method='POST'):
    """One HTTP request, written by hand so that a test can also send what a
    real client would not.  Answers (status, body)."""
    extra = ''.join(f'{k}: {v}\r\n' for k, v in (headers or {}).items())
    with socket.create_connection(('127.0.0.1', port), timeout=25) as s:
        s.sendall(f'{method} {path} HTTP/1.1\r\nHost: qrepo\r\n'
                  f'Content-Length: {len(payload)}\r\n{extra}\r\n'.encode() +
                  payload)
        data = b''
        while True:
            v = s.recv(65536)
            if not v: break
            data += v
    if not data: raise ConnectionError('the server closed the connection')
    line, _, rest = data.partition(b'\r\n')
    _, _, body = rest.partition(b'\r\n\r\n')
    return int(line.split()[1]), body


def frames(body):
    out, at = [], 0
    while at < len(body):
        n = struct.unpack('!I', body[at:at + 4])[0]
        out.append(body[at + 4:at + 4 + n])
        at += 4 + n
    return out


def rpc(port, body, objects=(), full=False, headers=None):
    """Protocol 3: a header frame (JSON, saying how many objects follow), then
    each object raw, as the body of a POST.  Answers the reply's header, or
    (header, objects).  A request the server could not answer at all (its
    worker failed) is a ConnectionError carrying the HTTP status."""
    body = dict(body, objects=len(objects))
    status, reply = post(
        port,
        frame(json.dumps(body).encode()) + b''.join(frame(o) for o in objects),
        headers)
    if status != 200: raise ConnectionError(f'HTTP {status}: {reply!r}')
    parts = frames(reply)
    header = json.loads(parts[0])
    return (header, parts[1:]) if full else header


def ready(port, proc):
    for _ in range(100):
        assert proc.poll() is None, 'server exited'
        try:
            if rpc(port, dict(version=3, op='head'))['ok']: return
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
                dict(version=3, op='push', expected='stale', head='0' * 64))
            assert not answer['ok']
            for objects in [[], [b'blob\nbad'],
                            [b'neither a blob nor a tree']]:
                try:
                    answer = rpc(
                        port,
                        dict(version=3,
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
                        dict(version=3, op='push', expected=before, head=cid),
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
            # a header that promises objects which never arrive; a frame cut
            # short; a header that is not JSON: the worker fails, the
            # listener answers 500 and goes on
            for payload in [
                    frame(json.dumps(dict(version=3, op='head', objects=3)).encode()),
                    struct.pack('!I', 1 << 30) + b'short',
                    frame(b'not json')]:
                status, why = post(port, payload)
                assert status == 500, (status, why)
            # what is not a QRepo request at all
            assert post(port, b'', path='/elsewhere')[0] == 404
            assert post(port, b'', method='GET')[0] == 405
            with socket.create_connection(('127.0.0.1', port), timeout=25) as s:
                s.sendall(b'POST /qrepo HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n')
                assert s.recv(64).startswith(b'HTTP/1.1 411')
            with socket.create_connection(('127.0.0.1', port), timeout=25) as s:
                s.sendall(b'POST /qrepo HTTP/1.1\r\nX: ' + b'y' * 70000)
                assert s.recv(64).startswith(b'HTTP/1.1 431')
            # a body that stops short of its Content-Length, then a hang-up
            with socket.create_connection(('127.0.0.1', port), timeout=25) as s:
                s.sendall(b'POST /qrepo HTTP/1.1\r\nContent-Length: 1000\r\n\r\nshort')
            assert rpc(port, dict(version=3, op='head'))['head'] == before
            # one request at a time: the hang-up above was finished, and its spool emptied
            assert not list((server / '.qrepo/spool/serve').iterdir())
            assert not rpc(port, dict(version=999, op='head'))['ok']
            assert not rpc(port, dict(version=1, op='head'))['ok']
            assert not rpc(port, dict(version=2, op='head'))['ok']
            # a fetch carries what the asker lacks: with everything, nothing; with nothing, all of it
            header, everything = rpc(port,
                                     dict(version=3, op='fetch', have=[]),
                                     full=True)
            header, nothing = rpc(port,
                                  dict(version=3, op='fetch', have=[before]),
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
                               dict(version=3,
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
            assert rpc(port, dict(version=3, op='head'))['head'] == before
            print('Server restart preserves published history: PASS',
                  flush=True)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            log.close()
