#!/usr/bin/env python3
"""Text merging and sync (2026-10-04): device names, measured changes, the
line merge and its rules, every conflict kind, sync's states, and blame.
Disposable roots, an ephemeral port."""

import json, os, pathlib, socket, subprocess, sys, tempfile, time

HOME = pathlib.Path(__file__).resolve().parents[1]
QR = os.environ.get('QR', str(HOME / 'qr'))


def run(root, *args, code=0):
    p = subprocess.run([QR, '--root', str(root), *args], capture_output=True, timeout=120)
    out = (p.stdout + p.stderr).decode('utf8', 'replace')
    assert p.returncode == code, (args, p.returncode, out)
    return p.stdout.decode('utf8', 'replace') if args[-1] == '--json' else out


def js(root, *args, code=0):
    return json.loads(run(root, *args, '--json', code=code))


def write(root, name, text):
    (root / name).write_bytes(text.encode() if isinstance(text, str) else text)


def read(root, name):
    return (root / name).read_text()


with tempfile.TemporaryDirectory(prefix='qrepo-textsync-test-') as d:
    base = pathlib.Path(d)
    srv, a, b = base / 'srv', base / 'a', base / 'b'
    for p in (srv, a, b):
        p.mkdir()
    run(srv, 'init')
    write(srv, 'todo.txt', 'Groceries\n- milk\n- eggs\n- bread\n- rice\n- tea\n')
    write(srv, 'prefs.json', '{"theme": "light", "font": 12}')
    write(srv, 'photo.bin', b'\x00\x01binary')
    write(srv, 'gone.txt', 'soon edited and deleted\n')
    run(srv, 'commit', 'seed')
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    server = subprocess.Popen([QR, '--root', str(srv), 'serve', str(port)], stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(('127.0.0.1', port), timeout=.2).close()
                break
            except OSError:
                time.sleep(.05)
        run(a, 'clone', str(port))
        run(b, 'clone', str(port))

        # device names, kept per device, carried by commits
        run(a, 'config', 'device', 'laptop')
        run(b, 'config', 'device', 'phone')
        assert json.loads(run(a, 'config'))['device'] == 'laptop'
        run(a, 'config', 'device', '', code=1)
        run(a, 'config', 'nonsense', code=2)

        # status --json measures what changed
        write(a, 'todo.txt', 'Groceries\n- oat milk\n- eggs\n- bread\n- rice\n- tea\n- apples\n')
        write(a, 'photo.bin', b'\x00\x02binary')
        write(a, 'new.txt', 'a\nb\n')
        st = js(a, 'status')
        ch = {c['path']: c for c in st['changes']}
        assert st['device'] == 'laptop' and st['sync'] == 'same' and not st['conflicts'], st
        assert ch['todo.txt'] == dict(path='todo.txt', change='M', text=True, added=2, removed=1, lines=6), ch
        assert ch['photo.bin']['text'] is False and ch['photo.bin']['change'] == 'M'
        assert ch['new.txt']['change'] == 'A' and ch['new.txt']['added'] == 2
        run(a, 'restore', 'HEAD', 'photo.bin', '--force')
        (a / 'new.txt').unlink()

        # sync: commit, push; an auto message names what changed
        r = js(a, 'sync')
        assert r['state'] == 'synced' and r['committed'] and r['pushed'] == r['committed'], r
        h = js(a, 'history')
        assert h[0]['author'] == 'laptop' and h[0]['message'] == 'auto: todo.txt +2 -1', h[0]
        assert js(a, 'status')['sync'] == 'same'
        r = js(a, 'sync')
        assert r['state'] == 'synced' and not r['committed'] and not r['pushed'], r

        # edits with a kept line between them merge, rules or not
        write(b, 'todo.txt', 'Groceries\n- milk\n- eggs\n- rye bread\n- rice\n- tea\n')
        r = js(b, 'sync')
        assert r['state'] == 'synced' and r['pulled'] == 'merged', r
        assert read(b, 'todo.txt') == 'Groceries\n- oat milk\n- eggs\n- rye bread\n- rice\n- tea\n- apples\n'
        js(a, 'sync')
        assert read(a, 'todo.txt') == read(b, 'todo.txt')
        print('Device names, measured changes, sync, and merges of different lines: PASS', flush=True)

        # both append: a conflict without the rules, a merge with them, and
        # sync retries by itself once the rules change
        write(a, 'todo.txt', read(a, 'todo.txt') + '- pears\n')
        js(a, 'sync')
        write(b, 'todo.txt', read(b, 'todo.txt') + '- plums\n')
        r = js(b, 'sync', code=3)
        assert r['state'] == 'conflict' and r['conflicts'][0]['kind'] == 'text', r
        hunk = r['conflicts'][0]['hunks'][0]
        assert hunk['base']['text'] == [] and hunk['ours']['text'] == ['- plums\n'] and hunk['theirs']['text'] == ['- pears\n'], hunk
        assert js(b, 'status')['conflicts'] and js(b, 'conflicts')['files'][0]['path'] == 'todo.txt'
        assert read(b, 'todo.txt').endswith('- plums\n')  # nothing was touched
        run(b, 'config', 'merge-rules', 'on')
        r = js(b, 'sync')
        assert r['state'] == 'synced' and r['pulled'] == 'merged', r
        assert read(b, 'todo.txt').endswith('- pears\n- plums\n'), read(b, 'todo.txt')  # the remote's lines first
        assert run(b, 'conflicts').strip() == 'no conflicts'
        js(a, 'sync')
        print("Concurrent appends: a conflict without the rules, the remote's first with them: PASS", flush=True)

        # the same word changed two ways is a conflict, rules or not; while
        # someone edits the resolution, sync touches nothing
        write(a, 'todo.txt', read(a, 'todo.txt').replace('- eggs', '- 6 eggs'))
        js(a, 'sync')
        write(b, 'todo.txt', read(b, 'todo.txt').replace('- eggs', '- 12 eggs'))
        r = js(b, 'sync', code=3)
        hunk = r['conflicts'][0]['hunks'][0]
        assert hunk['ours']['text'] == ['- 12 eggs\n'] and hunk['theirs']['text'] == ['- 6 eggs\n'], hunk
        remote = js(b, 'conflicts')['remote']
        theirs = subprocess.run([QR, '--root', str(b), 'show', remote, 'todo.txt'], capture_output=True).stdout.decode()
        write(b, 'todo.txt', theirs.replace('- 6 eggs', '- 6 or 12 eggs'))
        before = js(b, 'history')[0]['id']
        r = js(b, 'sync', code=3)
        assert not r['committed'] and js(b, 'history')[0]['id'] == before
        assert '6 or 12 eggs' in read(b, 'todo.txt')
        run(b, 'commit-merge', 'eggs: both')
        assert not js(b, 'status')['conflicts']
        assert js(b, 'sync')['state'] == 'synced'
        js(a, 'sync')
        assert read(a, 'todo.txt') == read(b, 'todo.txt') and '- 6 or 12 eggs\n' in read(a, 'todo.txt')
        print('Same line changed two ways: a conflict; a resolution in progress is left alone: PASS', flush=True)

        # every kind of conflict is named, and all files are reported at once
        write(a, 'prefs.json', '{"theme": "dark", "font": 12}')
        write(a, 'photo.bin', b'\x00A')
        write(a, 'gone.txt', 'edited\n')
        js(a, 'sync')
        write(b, 'prefs.json', '{"theme": "blue", "font": 12}')
        write(b, 'photo.bin', b'\x00B')
        (b / 'gone.txt').unlink()
        r = js(b, 'sync', code=3)
        kinds = {c['path']: c['kind'] for c in r['conflicts']}
        assert kinds == {'prefs.json': 'json', 'photo.bin': 'binary', 'gone.txt': 'delete/modify'}, kinds
        # resolve: take theirs for all three
        remote = js(b, 'conflicts')['remote']
        for f in ('prefs.json', 'photo.bin', 'gone.txt'):
            run(b, 'restore', remote, f, '--force')
        run(b, 'commit-merge', 'take theirs')
        js(b, 'sync')
        js(a, 'sync')
        # JSON fields that do not overlap still merge
        write(a, 'prefs.json', '{"theme": "dark", "font": 14}')
        js(a, 'sync')
        write(b, 'prefs.json', '{"theme": "dark", "font": 12, "lang": "en"}')
        assert js(b, 'sync')['pulled'] == 'merged'
        assert json.loads(read(b, 'prefs.json')) == {'theme': 'dark', 'font': 14, 'lang': 'en'}
        js(a, 'sync')
        # a file added on both sides merges as text from nothing, with the rules
        write(a, 'ideas.txt', 'from a\n')
        js(a, 'sync')
        write(b, 'ideas.txt', 'from b\n')
        assert js(b, 'sync')['pulled'] == 'merged'
        assert sorted(read(b, 'ideas.txt').splitlines()) == ['from a', 'from b']
        js(a, 'sync')
        print('Conflict kinds named together (text, json, binary, delete/modify); JSON fields and double adds merge: PASS', flush=True)

        # blame: each line's checkpoint and device; uncommitted lines have none
        write(a, 'todo.txt', read(a, 'todo.txt') + '- not yet committed\n')
        bl = js(a, 'blame', 'todo.txt')
        lines = read(a, 'todo.txt').splitlines()
        owner = {}
        for rg in bl['ranges']:
            for i in range(rg['start'], rg['end']):
                owner[lines[i]] = rg['commit']
        assert bl['lines'] == len(lines) and owner['- not yet committed'] is None
        who = lambda line: bl['commits'][owner[line]]['author']
        assert who('Groceries') == 'local' and who('- rye bread') == 'phone' and who('- pears') == 'laptop', bl
        assert who('- plums') == 'phone' and who('- 6 or 12 eggs') == 'phone'
        assert run(a, 'blame', 'todo.txt').splitlines()[-1].startswith('uncommitted')
        run(a, 'blame', 'missing.txt', code=1)
        print('Blame: lines traced through merges to their checkpoint and device: PASS', flush=True)

        # resolve: the merge with each conflicting region picked; commit-merge
        # finishes it, bringing the remote's changes to other files along
        js(a, 'sync')
        js(b, 'sync')
        write(a, 'todo.txt', read(a, 'todo.txt').replace('- tea', '- green tea'))
        write(a, 'ideas.txt', read(a, 'ideas.txt') + 'laptop idea\n')
        js(a, 'sync')
        write(b, 'todo.txt', read(b, 'todo.txt').replace('- tea', '- black tea').replace('Groceries', 'GROCERIES'))
        r = js(b, 'sync', code=3)
        rec = js(b, 'conflicts')
        assert (rec['localAuthor'], rec['remoteAuthor']) == ('phone', 'laptop'), rec
        for pick, want in [('ours', '- black tea\n'), ('theirs', '- green tea\n'), ('both', '- green tea\n- black tea\n')]:
            run(b, 'resolve', 'todo.txt', pick)
            t = read(b, 'todo.txt')
            assert want in t and t.startswith('GROCERIES') and ('black' in want or 'black' not in t), (pick, t)
        run(b, 'resolve', 'todo.txt', 'sideways', code=2)
        run(b, 'resolve', 'ideas.txt', 'ours', code=1)
        assert 'laptop idea' not in read(b, 'ideas.txt')
        write(b, 'stray.txt', 'x')
        assert 'changed outside the conflicting files: stray.txt' in run(b, 'commit-merge', 'tea', code=1)
        (b / 'stray.txt').unlink()
        run(b, 'commit-merge', 'tea: both')
        assert 'laptop idea' in read(b, 'ideas.txt') and not js(b, 'status')['conflicts']
        assert js(b, 'status')['changes'] == []
        h = js(b, 'history')[0]
        assert len(h['parents']) == 2 and h['author'] == 'phone' and h['message'] == 'tea: both'
        js(b, 'sync')
        js(a, 'sync')
        assert read(a, 'todo.txt') == read(b, 'todo.txt') and read(a, 'ideas.txt') == read(b, 'ideas.txt')
        print('Resolve picks ours, theirs or both; commit-merge finishes the whole merge: PASS', flush=True)

        # without a remote, sync commits and says so
        solo = base / 'solo'
        solo.mkdir()
        run(solo, 'init')
        write(solo, 'n.txt', 'x\n')
        r = js(solo, 'sync')
        assert r['state'] == 'local' and r['committed'], r
        print('Sync without a remote: PASS', flush=True)
    finally:
        server.terminate()
        server.wait(timeout=10)
