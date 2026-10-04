#!/usr/bin/env python3
"""A person keeping two notes files on two devices, for a few minutes.

    tests/notes_sim.py WORKDIR REMOTE TOKENFILE [--seconds N] [--seed S]

Device A is where they mostly write: bursts of appends, edits in the middle,
inserted and deleted lines, with pauses between.  Device B is another
machine that now and then edits too, sometimes the same file at the same
time.  Interleaved with that, the things a person means by "undo":

  - delete a file by mistake, then get it back (before and after committing)
  - make a pile of edits, then throw them all away
  - go back several checkpoints for one file, and keep going from there
  - get a deleted file back on the OTHER device

Every checkpoint's expected text is recorded, and checked again at random
later on with `show`, so history itself is verified, not just the end state.
Writes RESULTS.json in WORKDIR.  Exits non-zero on the first mismatch.
"""

import argparse, json, os, pathlib, random, subprocess, sys, time

HOME = pathlib.Path(__file__).resolve().parents[1]
QR = os.environ.get('QR', str(HOME / 'qr'))
FILES = ['journal.txt', 'todo.txt']

ap = argparse.ArgumentParser()
ap.add_argument('workdir')
ap.add_argument('remote')
ap.add_argument('tokenfile')
ap.add_argument('--seconds', type=float, default=300)
ap.add_argument('--seed', type=int, default=20261004)
args = ap.parse_args()
rng = random.Random(args.seed)
work = pathlib.Path(args.workdir)
work.mkdir(parents=True, exist_ok=False)
A, B = work / 'device-a', work / 'device-b'
A.mkdir()
B.mkdir()
log = (work / 'commands.log').open('w')
counts = {}
timings = {}
checkpoints = {}  # commit id -> {file: text or None}
mine = []  # every commit this run made, for the caller's cleanup
started = time.time()


def bump(k, n=1):
    counts[k] = counts.get(k, 0) + n


def qr(root, *a, ok=True, stdin=None):
    t = time.time()
    p = subprocess.run([QR, '--root', str(root), *a], capture_output=True, input=stdin, timeout=300)
    took = time.time() - t
    out = (p.stdout + p.stderr).decode('utf8', 'replace')
    log.write(json.dumps(dict(t=round(t - started, 2), dev=root.name, args=a, rc=p.returncode, ms=round(took * 1000), out=out[-300:])) + '\n')
    log.flush()
    timings.setdefault(a[0], []).append(took)
    if (p.returncode == 0) != ok:
        fail(f'{root.name}: qr {" ".join(a)} -> rc {p.returncode}\n{out}')
    return out


def fail(why):
    print('FAIL:', why, flush=True)
    write_results('FAIL', why)
    sys.exit(1)


def read(root, f):
    p = root / f
    return p.read_text() if p.exists() else None


def state(root):
    return {f: read(root, f) for f in FILES}


def head(root):
    return (root / '.qrepo/HEAD').read_text().strip()


def commit(root, message):
    out = qr(root, 'commit', message)
    cid = out.strip().split()[-1]
    checkpoints[cid] = state(root)
    mine.append(cid)
    if not qr(root, 'status').startswith('clean'):
        fail(f'{root.name}: not clean right after commit')
    bump('commits')
    return cid


def expect(root, want, what):
    got = state(root)
    if got != want:
        diff = {f: (want[f], got[f]) for f in FILES if want[f] != got[f]}
        fail(f'{what}: {root.name} differs: {json.dumps(diff)[:600]}')


def check_history(n=3):
    """`show` at random earlier checkpoints returns exactly what was there."""
    for cid in rng.sample(sorted(checkpoints), min(n, len(checkpoints))):
        for f, text in checkpoints[cid].items():
            if text is None:
                qr(A, 'show', cid, f, ok=False)
            else:
                p = subprocess.run([QR, '--root', str(A), 'show', cid, f], capture_output=True)
                if p.returncode or p.stdout.decode() != text:
                    fail(f'show {cid[:12]} {f} is not what was committed')
        bump('history_checks')


# ---- the writer's edits ------------------------------------------------------
WORDS = ('call the plumber review notes from tuesday buy coffee beans draft the '
         'quarterly summary fix the bike light reply to sam renew passport book '
         'dentist read chapter four water the plants pay the electricity bill').split()


def sentence():
    return ' '.join(rng.choice(WORDS) for _ in range(rng.randint(3, 9))).capitalize() + '.'


def edit(root, f):
    text = read(root, f) or ''
    lines = text.split('\n')[:-1] if text else []
    op = rng.choice(['append', 'append', 'append', 'middle', 'insert', 'delete', 'paragraph'])
    if op == 'append' or not lines:
        lines.append(f'- {sentence()}')
    elif op == 'middle':
        i = rng.randrange(len(lines))
        lines[i] = lines[i] + ' (edited: ' + sentence() + ')'
    elif op == 'insert':
        lines.insert(rng.randrange(len(lines) + 1), f'* {sentence()}')
    elif op == 'delete':
        del lines[rng.randrange(len(lines))]
    else:
        i = rng.randrange(len(lines))
        lines[i:i + 3] = [sentence() for _ in range(rng.randint(1, 4))]
    (root / f).write_text('\n'.join(lines) + '\n' if lines else '')
    bump('edit:' + op)


def burst(root, label):
    for _ in range(rng.randint(2, 8)):
        edit(root, rng.choice(FILES))
    if qr(root, 'status').startswith('clean'):
        edit(root, FILES[0])
    return commit(root, label)


def push(root):
    out = qr(root, 'push')
    bump('pushes')
    return out


def pull(root):
    out = qr(root, 'pull')
    bump('pulls')
    return out


def converge():
    """Both devices pull until they agree with each other and the remote."""
    pull(A)
    pull(B)
    if head(A) != head(B):
        fail(f'heads differ after sync: {head(A)[:12]} {head(B)[:12]}')
    expect(B, state(A), 'converge')
    bump('converged')


# ---- undo, from the person's point of view ------------------------------------
def undo_delete_uncommitted(root):
    f = rng.choice(FILES)
    before = state(root)
    (root / f).unlink()
    if 'D ' + f not in qr(root, 'status'):
        fail('a deleted file is not reported')
    qr(root, 'restore', 'HEAD', f)
    expect(root, before, 'undo an uncommitted delete')
    bump('undo:uncommitted-delete')


def undo_delete_committed(root):
    f = rng.choice(FILES)
    before = state(root)
    keep = head(root)
    (root / f).unlink()
    commit(root, f'delete {f}')
    push(root)
    # later: "where did my notes go" -- bring it back from before the delete
    qr(root, 'restore', keep, f)
    expect(root, before, 'undo a committed delete')
    commit(root, f'bring back {f}')
    push(root)
    bump('undo:committed-delete')


def discard_pile_of_edits(root):
    before = state(root)
    for _ in range(rng.randint(5, 15)):
        edit(root, rng.choice(FILES))
    if rng.random() < .5 and (root / FILES[1]).exists():
        (root / FILES[1]).unlink()
    # a plain restore refuses to throw work away...
    for f in FILES:
        if before[f] is not None and read(root, f) not in (None, before[f]):
            qr(root, 'restore', 'HEAD', f, ok=False)
            break
    # ...and --force is "discard my changes"
    for f in FILES:
        if before[f] is not None and read(root, f) != before[f]:
            qr(root, 'restore', 'HEAD', f, '--force')
    expect(root, before, 'discard a pile of edits')
    if not qr(root, 'status').startswith('clean'):
        fail('not clean after discarding')
    bump('undo:discard-edits')


def roll_back_several(root):
    """Go back N checkpoints for one file and carry on from there."""
    ids = [line.split()[0] for line in qr(root, 'history').splitlines()]
    if len(ids) < 4:
        return
    back = ids[rng.randint(2, min(8, len(ids) - 1))]
    f = rng.choice([g for g in FILES if checkpoints.get(back, {}).get(g) is not None] or [None])
    if f is None:
        return
    want = dict(state(root), **{f: checkpoints[back][f]})
    qr(root, 'restore', back, f, '--force')
    expect(root, want, f'roll {f} back to {back[:12]}')
    if want != checkpoints[head(root)]:
        commit(root, f'roll {f} back')
    edit(root, f)
    commit(root, f'carry on from the old {f}')
    push(root)
    bump('undo:roll-back')


def restore_on_other_device():
    """A deletes a file and syncs; B, later, wants it back."""
    converge()
    f = rng.choice(FILES)
    before = state(A)
    if before[f] is None:
        return
    keep = head(A)
    (A / f).unlink()
    commit(A, f'delete {f} on A')
    push(A)
    pull(B)
    if (B / f).exists():
        fail('a delete did not reach the other device')
    qr(B, 'restore', keep, f)
    expect(B, before, 'restore on the other device')
    commit(B, f'B brings back {f}')
    push(B)
    pull(A)
    expect(A, before, 'restored file reaches the first device')
    bump('undo:other-device')


def same_file_conflict():
    """Both devices edit the same file before syncing: the second to sync
    gets a refusal, merges by hand, and nothing is lost."""
    converge()
    f = rng.choice(FILES)
    edit(A, f)
    commit(A, f'A edits {f}')
    push(A)
    mark = f'- B wrote this offline ({rng.randrange(10**6)})'
    text = read(B, f) or ''
    (B / f).write_text(text + mark + '\n')
    commit(B, f'B edits {f}')
    out = qr(B, 'push', ok=False)
    if 'stale push' not in out:
        fail('a stale push was not refused: ' + out)
    out = qr(B, 'pull', ok=False)
    if 'merge conflict' not in out:
        fail('a same-file edit merged silently: ' + out)
    # their resolution: take A's version and add their own line back
    remote = (B / '.qrepo/REMOTE_HEAD').read_text().strip()
    theirs = subprocess.run([QR, '--root', str(B), 'show', remote, f], capture_output=True).stdout.decode()
    (B / f).write_text(theirs + mark + '\n')
    out = qr(B, 'commit-merge', f'B merges {f}')
    cid = out.strip().split()[-1]
    checkpoints[cid] = state(B)
    mine.append(cid)
    push(B)
    pull(A)
    if mark not in read(A, f) or theirs not in read(A, f):
        fail('a hand merge lost one side')
    expect(A, state(B), 'after a hand merge')
    bump('conflicts-resolved')


def other_file_on_b():
    """B edits the file A is not touching; both sync; it merges on its own."""
    converge()
    edit(A, FILES[0])
    commit(A, 'A journal')
    push(A)
    edit(B, FILES[1])
    commit(B, 'B todo')
    qr(B, 'push', ok=False)
    out = pull(B)
    if 'merged remote' not in out:
        fail('a different-file edit did not merge: ' + out)
    checkpoints[head(B)] = state(B)
    mine.append(head(B))
    push(B)
    converge()
    bump('auto-merges')


def write_results(result, why=''):
    json.dump(dict(result=result, why=why, seconds=round(time.time() - started, 1), remote=args.remote,
                   counts=counts, checkpoints=len(checkpoints),
                   timings_ms={k: dict(n=len(v), median=round(sorted(v)[len(v) // 2] * 1000), max=round(max(v) * 1000))
                               for k, v in timings.items()},
                   commits=mine),
              (work / 'RESULTS.json').open('w'), indent=1)


# ---- the session -------------------------------------------------------------
token = pathlib.Path(args.tokenfile).read_bytes()
qr(A, 'clone', args.remote, '--token-stdin', stdin=token)
qr(B, 'clone', args.remote, '--token-stdin', stdin=token)
checkpoints[head(A)] = state(A)
for f in FILES:
    (A / f).write_text(f'# {f[:-4]}\n')
commit(A, 'start notes')
push(A)
pull(B)

scenarios = [undo_delete_uncommitted, undo_delete_committed, discard_pile_of_edits,
             roll_back_several, restore_on_other_device, same_file_conflict, other_file_on_b]
rounds = 0
while time.time() - started < args.seconds:
    rounds += 1
    burst(A, f'burst {rounds}')
    if rng.random() < .6:
        push(A)
    if rounds % 2 == 0:
        scenario = scenarios[(rounds // 2 - 1) % len(scenarios)]
        print(f'[{time.time() - started:6.1f}s] round {rounds}: {scenario.__name__}', flush=True)
        if scenario in (undo_delete_uncommitted, discard_pile_of_edits):
            scenario(A)
        elif scenario in (undo_delete_committed, roll_back_several):
            push(A)
            scenario(A)
        else:
            push(A)
            scenario()
    check_history()
    time.sleep(rng.uniform(1, 6))

push(A)
converge()
check_history(len(checkpoints))
write_results('PASS')
print(f'PASS: {rounds} rounds, {len(mine)} commits, {json.dumps(counts)}', flush=True)
