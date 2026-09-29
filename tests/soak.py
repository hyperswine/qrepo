#!/usr/bin/env python3
"""Three-minute CLI experiment. All writes are beneath a NEW experiment directory.
Leaves the loopback server running on success and records its PID and logs.
Usage: python3 tests/soak.py DIRECTORY [--seconds 180] [--port 2005]
"""
import argparse, hashlib, json, os, pathlib, statistics, subprocess, time
from remote import ready  # helpers only; remote.py's suite is guarded by __main__
parser=argparse.ArgumentParser();parser.add_argument('directory',type=pathlib.Path);parser.add_argument('--seconds',type=float,default=180);parser.add_argument('--port',type=int,default=2005)
args=parser.parse_args();home=pathlib.Path(__file__).resolve().parents[1];qr=os.environ.get('QR',str(home/'qr'))
root=args.directory.resolve();root.mkdir(parents=True,exist_ok=False)
server=root/'server';alice=root/'alice';bob=root/'bob'
for p in [server,alice,bob]:p.mkdir()
trace=(root/'commands.jsonl').open('w',buffering=1);events=[];timings={};counts=dict(stale_push_refusals=0,merge_conflicts=0,dirty_pull_refusals=0,cycles=0)
started=time.monotonic()
def call(repo,*command,ok=True):
    at=time.monotonic();p=subprocess.run([qr,'--root',str(repo),*command],capture_output=True,text=True,timeout=30)
    elapsed=time.monotonic()-at
    event=dict(at_seconds=at-started,repo=repo.name,args=command,expected_success=ok,code=p.returncode,seconds=elapsed,stdout=p.stdout,stderr=p.stderr)
    trace.write(json.dumps(event)+'\n');events.append(event);timings.setdefault(command[0],[]).append(elapsed)
    if (p.returncode==0)!=ok: raise AssertionError(event)
    return p.stdout+p.stderr
def head(p):return (p/'.qrepo/HEAD').read_text()
def state(p):return json.loads((p/'state.json').read_text())
def write_state(p,v):(p/'state.json').write_text(json.dumps(v,sort_keys=True)+'\n')
call(server,'init');write_state(server,dict(alice=0,bob=0,shared=0));(server/'binary.dat').write_bytes(bytes(range(256))*4)
call(server,'commit','seed experiment')
log=(root/'server.log').open('w')
proc=subprocess.Popen([qr,'--root',str(server),'serve',str(args.port)],stdout=log,stderr=log,start_new_session=True)
(root/'server.pid').write_text(str(proc.pid)+'\n');(root/'server-command.json').write_text(json.dumps(proc.args,indent=2)+'\n')
ready(args.port,proc);call(alice,'clone',str(args.port));call(bob,'clone',str(args.port))
loop_start=time.monotonic();cycles=12
try:
    for i in range(1,cycles+1):
        a=state(alice);b=state(bob);a['alice']=i;b['bob']=i
        conflict=i%4==0
        if conflict:a['shared']=i*2;b['shared']=i*2+1
        write_state(alice,a);write_state(bob,b)
        (alice/'alice.txt').write_text(f'Alice iteration {i}\n');(bob/'bob.txt').write_text(f'Bob iteration {i}\n')
        if i>1:(alice/f'round-{i-1:02}.txt').unlink()
        (alice/f'round-{i:02}.txt').write_text(f'created in round {i}\n')
        call(alice,'commit',f'Alice round {i}');call(bob,'commit',f'Bob round {i}');call(alice,'push')
        before=head(server);out=call(bob,'push',ok=False);assert 'stale' in out and head(server)==before;counts['stale_push_refusals']+=1
        if conflict:
            before=head(bob);saved=(bob/'state.json').read_bytes();out=call(bob,'pull',ok=False)
            assert 'merge conflict' in out and head(bob)==before and (bob/'state.json').read_bytes()==saved
            counts['merge_conflicts']+=1
            # Explicit project policy: retain both counters and Bob's shared value.
            # Fetch has supplied Alice's tree; preserve all her independent changes
            # by importing her known round file/removal and current note via show.
            remote=(bob/'.qrepo/REMOTE_HEAD').read_text()
            for name in ['alice.txt',f'round-{i:02}.txt']:
                out=call(bob,'show',remote,name)
                (bob/name).write_text(out)
            if i>1:(bob/f'round-{i-1:02}.txt').unlink()
            resolved=dict(alice=i,bob=i,shared=i*2+1);write_state(bob,resolved)
            call(bob,'commit-merge',f'Resolve shared field in round {i}')
        else:call(bob,'pull')
        call(bob,'push');call(alice,'pull')
        assert head(alice)==head(bob)==head(server)
        assert state(alice)==state(bob) and state(alice)['alice']==i and state(alice)['bob']==i
        assert (alice/'bob.txt').read_bytes()==(bob/'bob.txt').read_bytes()
        assert (alice/'alice.txt').read_bytes()==(bob/'alice.txt').read_bytes()
        if i%3==0:
            p=alice/'uncommitted.txt';p.write_text('keep this edit')
            before=head(alice);out=call(alice,'pull',ok=False);assert 'dirty' in out and head(alice)==before and p.read_text()=='keep this edit'
            counts['dirty_pull_refusals']+=1;p.unlink()
        for repo in [alice,bob]:assert 'clean' in call(repo,'status')
        counts['cycles']=i
        print(json.dumps(dict(cycle=i,elapsed_s=round(time.monotonic()-loop_start,2),head=head(server),state=state(alice),counts=counts)),flush=True)
        remaining=loop_start+args.seconds*i/cycles-time.monotonic()
        if remaining>0:time.sleep(remaining)
    def audit(repo):
        seen=set();commits=set();todo=[head(repo)]
        def obj(oid,kind):
            raw=(repo/'.qrepo/objects'/oid).read_bytes();assert hashlib.sha256(raw).hexdigest()==oid and raw.startswith(kind+b'\n');seen.add(oid);return raw[len(kind)+1:]
        while todo:
            oid=todo.pop()
            if oid in commits:continue
            commits.add(oid);rec=json.loads(obj(oid,b'commit'));tree=json.loads(obj(rec['root_state'],b'tree'))
            for name,bid in tree.items():assert not name.startswith('/') and '..' not in name.split('/');obj(bid,b'blob')
            todo.extend(rec['parents'])
        rec=json.loads(obj(head(repo),b'commit'));tree=json.loads(obj(rec['root_state'],b'tree'))
        if repo!=server:
            actual={str(p.relative_to(repo)):p.read_bytes() for p in repo.rglob('*') if p.is_file() and '.qrepo' not in p.relative_to(repo).parts}
            expected={p:obj(oid,b'blob') for p,oid in tree.items()};assert actual==expected
        return commits,seen
    audits=[audit(p) for p in [server,alice,bob]];assert audits[0]==audits[1]==audits[2]
    stats={name:dict(count=len(xs),median_ms=round(statistics.median(xs)*1000,2),max_ms=round(max(xs)*1000,2)) for name,xs in timings.items()}
    result=dict(result='PASS',duration_seconds=round(time.monotonic()-loop_start,2),endpoint=f'127.0.0.1:{args.port}',server_pid=proc.pid,counts=counts,commands=len(events),head=head(server),state=state(alice),reachable_commits=len(audits[0][0]),reachable_objects=len(audits[0][1]),all_reachable_objects_verified=True,working_trees_match=True,timings=stats)
    (root/'RESULTS.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2),flush=True)
except BaseException:
    (root/'FAILED.json').write_text(json.dumps(dict(counts=counts,server_pid=proc.pid,last_events=events[-5:]),indent=2)+'\n')
    proc.terminate();proc.wait(timeout=10);raise
finally:trace.close();log.close()
