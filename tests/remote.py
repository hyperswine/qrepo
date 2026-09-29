#!/usr/bin/env python3
"""Native server/CLI regressions, using disposable roots and an ephemeral port."""
import concurrent.futures, base64, hashlib, json, os, pathlib, socket, struct, subprocess, tempfile, time
HOME=pathlib.Path(__file__).resolve().parents[1]
QR=os.environ.get('QR',str(HOME/'qr'))
def cmd(root,*args,ok=True):
    p=subprocess.run([QR,'--root',str(root),*args],capture_output=True,text=True,timeout=30)
    assert (p.returncode==0)==ok,(args,p.returncode,p.stdout,p.stderr)
    return p.stdout+p.stderr
def head(root): return (root/'.qrepo/HEAD').read_text()
def rpc(port,body):
    data=json.dumps(body).encode()
    with socket.create_connection(('127.0.0.1',port),timeout=5) as s:
        s.sendall(struct.pack('!I',len(data))+data)
        def read(n):
            out=b''
            while len(out)<n:
                v=s.recv(n-len(out))
                if not v: raise ConnectionError('worker closed connection')
                out+=v
            return out
        n=struct.unpack('!I',read(4))[0]
        return json.loads(read(n))
def ready(port,proc):
    for _ in range(100):
        assert proc.poll() is None,'server exited'
        try:
            if rpc(port,dict(version=1,op='head'))['ok']: return
        except (OSError,ConnectionError): pass
        time.sleep(.05)
    raise AssertionError('server not ready')
if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='qrepo-remote-test-') as d:
        root=pathlib.Path(d); server=root/'server';a=root/'alice';b=root/'bob'
        for p in [server,a,b]:p.mkdir()
        with socket.socket() as s: s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        cmd(server,'init');(server/'state.json').write_text('{"alice":0,"bob":0}');(server/'binary').write_bytes(bytes(range(256)));cmd(server,'commit','seed')
        log=(root/'server.log').open('w')
        proc=subprocess.Popen([QR,'--root',str(server),'serve',str(port)],stdout=log,stderr=log,start_new_session=True)
        try:
            ready(port,proc)
            cmd(a,'clone',str(port));cmd(b,'clone',str(port));assert head(a)==head(b)==head(server)
            assert (a/'binary').read_bytes()==bytes(range(256))
            (a/'alice.txt').write_text('alice');cmd(a,'commit','alice');cmd(a,'push')
            (b/'bob.txt').write_text('bob');cmd(b,'commit','bob');before=head(server)
            assert 'stale' in cmd(b,'push',ok=False);assert head(server)==before
            cmd(b,'pull');cmd(b,'push');cmd(a,'pull');assert head(a)==head(b)==head(server)
            assert (a/'bob.txt').read_text()=='bob' and (b/'alice.txt').read_text()=='alice'
            (a/'state.json').write_text('{"alice":1,"bob":0}');cmd(a,'commit','alice json');cmd(a,'push')
            (b/'state.json').write_text('{"alice":0,"bob":1}');cmd(b,'commit','bob json');cmd(b,'pull');cmd(b,'push');cmd(a,'pull')
            assert json.loads((a/'state.json').read_text())=={'alice':1,'bob':1}
            (a/'alice.txt').unlink();cmd(a,'commit','delete');cmd(a,'push');cmd(b,'pull');assert not (b/'alice.txt').exists()
            (b/'bob.txt').write_text('dirty');prior=head(b);cmd(b,'pull',ok=False);assert head(b)==prior and (b/'bob.txt').read_text()=='dirty'
            cmd(b,'restore','HEAD','bob.txt','--force')
            # Same-field conflict preserves both committed history and working file.
            (a/'state.json').write_text('{"alice":2,"bob":1}');cmd(a,'commit','conflict a');cmd(a,'push')
            (b/'state.json').write_text('{"alice":3,"bob":1}');cmd(b,'commit','conflict b');prior=head(b)
            assert 'conflict' in cmd(b,'pull',ok=False);assert head(b)==prior and json.loads((b/'state.json').read_text())['alice']==3
            assert proc.poll() is None
            (b/'state.json').write_text('{"alice":4,"bob":1}')
            cmd(b,'commit-merge','manual resolution');cmd(b,'push');cmd(a,'pull')
            assert head(a)==head(b)==head(server) and json.loads((a/'state.json').read_text())['alice']==4
            print('Clone/binary transfer, stale push, disjoint merge, JSON merge, deletion, dirty/conflict preservation: PASS',flush=True)
            # Two actual simultaneous push processes: exactly one CAS succeeds.
            (a/'race-a').write_text('A');cmd(a,'commit','race A')
            (b/'race-b').write_text('B');cmd(b,'commit','race B')
            def push_process(repo): return subprocess.run([QR,'--root',str(repo),'push'],capture_output=True,timeout=30).returncode
            with concurrent.futures.ThreadPoolExecutor(2) as pool: codes=list(pool.map(push_process,[a,b]))
            assert sorted(codes)==[0,1],codes
            loser,winner=(a,b) if codes[0] else (b,a)
            cmd(loser,'pull');cmd(loser,'push');cmd(winner,'pull')
            assert head(a)==head(b)==head(server)
            print('Concurrent pushes: one winner, merged retry retains both changes: PASS',flush=True)
            fault=os.environ.get('QR_FAULT')
            if fault:
                (a/'bob.txt').write_text('crash-safe remote update');cmd(a,'commit','checkout crash');cmd(a,'push')
                prior=head(b)
                p=subprocess.run([fault,'--root',str(b),'pull'],env=dict(os.environ,QREPO_FAULT='after:bob.txt'),capture_output=True,timeout=30)
                assert p.returncode==86,(p.returncode,p.stdout,p.stderr)
                assert head(b)==prior and (b/'.qrepo/checkout.json').exists()
                cmd(b,'recover');assert head(b)==head(server) and (b/'bob.txt').read_text()=='crash-safe remote update'
                assert not (b/'.qrepo/checkout.json').exists()
                print('Interrupted multi-file checkout journal recovery: PASS',flush=True)
            # Refusal and worker isolation: malformed or invalid packs never advance HEAD.
            before=head(server)
            answer=rpc(port,dict(version=1,op='push',expected='stale',head='0'*64,objects=[]));assert not answer['ok']
            for body in [dict(version=1,op='push',expected=before,head='0'*64,objects=[]),dict(version=1,op='push',expected=before,head='0'*64,objects=[dict(id='0'*64,data=base64.b64encode(b'blob\nbad').decode())])]:
                try: answer=rpc(port,body)
                except ConnectionError: pass
                else: assert not answer['ok']
                assert head(server)==before
            def packed(kind,body):
                raw=kind.encode()+b'\n'+body;oid=hashlib.sha256(raw).hexdigest()
                return oid,dict(id=oid,data=base64.b64encode(raw).decode())
            bid,blob=packed('blob',b'escape')
            tid,tree=packed('tree',json.dumps({'../outside':bid}).encode())
            cid,commit=packed('commit',json.dumps(dict(root_state=tid,parents=[before],message='unsafe')).encode())
            try: answer=rpc(port,dict(version=1,op='push',expected=before,head=cid,objects=[blob,tree,commit]))
            except ConnectionError: pass
            else: assert not answer['ok']
            assert head(server)==before and not (root/'outside').exists()
            with socket.create_connection(('127.0.0.1',port)) as s:s.sendall(struct.pack('!I',16*1024*1024+1))
            assert rpc(port,dict(version=1,op='head'))['head']==before
            assert not rpc(port,dict(version=999,op='head'))['ok']
            print('Invalid packs, incomplete graphs, oversized frames, protocol version and listener survival: PASS',flush=True)
            proc.terminate();proc.wait(timeout=10)
            proc=subprocess.Popen([QR,'--root',str(server),'serve',str(port)],stdout=log,stderr=log,start_new_session=True)
            ready(port,proc);assert rpc(port,dict(version=1,op='head'))['head']==before
            print('Server restart preserves published history: PASS',flush=True)
        finally:
            proc.terminate();proc.wait(timeout=10);log.close()
