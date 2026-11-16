#!/usr/bin/env python3
import argparse,hashlib,json,os,subprocess,sys,tempfile
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parent
TARGET=Path('/opt/kalpa-delivery-dashboard')
NAMES=('dashboard.py','monitor_evidence.py')
def sha(data):return hashlib.sha256(data).hexdigest()
def atomic(path,data,mode):
 fd,name=tempfile.mkstemp(dir=path.parent,prefix='.kalpa-dash-')
 try:
  with os.fdopen(fd,'wb')as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.chmod(name,mode);os.replace(name,path)
 finally:
  if os.path.exists(name):os.unlink(name)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');ap.add_argument('--rollback',type=Path);args=ap.parse_args()
 if args.rollback:
  meta=json.loads((args.rollback/'manifest.json').read_text())
  for name in NAMES:
   if sha((TARGET/name).read_bytes())!=meta[name]['installed']:raise RuntimeError('Rollback refused: later changes to '+name)
  for name in NAMES:atomic(TARGET/name,(args.rollback/name).read_bytes(),meta[name]['mode'])
  print('Dashboard restored. Reopen dashboard view. Central Sync unchanged.');return
 for name,digest in json.loads((ROOT/'checksums.json').read_text()).items():
  if sha((ROOT/name).read_bytes())!=digest:raise RuntimeError('Package checksum mismatch: '+name)
 expected=json.loads((ROOT/'expected.json').read_text());old={};modes={}
 for name,digest in expected.items():
  p=TARGET/name;data=p.read_bytes();allowed={digest}
  if name in NAMES:allowed.add(sha((ROOT/name).read_bytes()))
  if sha(data)not in allowed:raise RuntimeError('Deployed dashboard differs from uploaded source: '+name)
  if name in NAMES:old[name]=data;modes[name]=p.stat().st_mode&0o777
 for p in ROOT.glob('*.py'):compile(p.read_bytes(),str(p),'exec')
 subprocess.run([sys.executable,'-B','-m','unittest','test_monitor','-q'],cwd=ROOT,check=True)
 if args.check_only:print('CHECK PASS: source, checksums, syntax and 8 read-only monitor tests. Nothing changed.');return
 backup=Path('/root/kalpa-dashboard-v16-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(mode=0o700)
 meta={name:{'installed':sha((ROOT/name).read_bytes()),'mode':modes[name]}for name in NAMES}
 for name in NAMES:(backup/name).write_bytes(old[name])
 (backup/'manifest.json').write_text(json.dumps(meta,indent=2));changed=[]
 try:
  for name in NAMES:
   p=TARGET/name
   if p.read_bytes()!=old[name]:raise RuntimeError('Concurrent edit detected: '+name)
   atomic(p,(ROOT/name).read_bytes(),modes[name]);changed.append(name)
 except Exception:
  for name in changed:
   if (TARGET/name).read_bytes()==(ROOT/name).read_bytes():atomic(TARGET/name,old[name],modes[name])
  raise
 print('Dashboard v1.6 installed. Backup:',backup)
 print('No service restart, queue writes or configuration changes.')
 print('Close old view with Ctrl+C and run: kalpa-delivery-dashboard')
 print('Rollback: python3 install.py --rollback',backup)
if __name__=='__main__':
 try:main()
 except Exception as exc:print('ERROR:',type(exc).__name__,str(exc),file=sys.stderr);sys.exit(1)
