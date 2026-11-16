#!/usr/bin/env python3
import argparse,hashlib,json,os,subprocess,sys,tempfile
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parent
TARGET=Path('/opt/kalpa-delivery-dashboard')
def sha(data):return hashlib.sha256(data).hexdigest()
def atomic(path,data,mode):
 fd,name=tempfile.mkstemp(dir=path.parent,prefix='.kalpa-dash-')
 try:
  with os.fdopen(fd,'wb')as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.chmod(name,mode);os.replace(name,path)
 finally:
  if os.path.exists(name):os.unlink(name)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');args=ap.parse_args()
 for name,digest in json.loads((ROOT/'checksums.json').read_text()).items():
  if sha((ROOT/name).read_bytes())!=digest:raise RuntimeError('Package checksum mismatch: '+name)
 expected=json.loads((ROOT/'expected.json').read_text());original={};modes={}
 for name,digest in expected.items():
  p=TARGET/name;data=p.read_bytes()
  if sha(data) not in (digest,sha((ROOT/name).read_bytes())):raise RuntimeError('Dashboard differs from verified v1.4: '+name)
  original[name]=data;modes[name]=p.stat().st_mode&0o777
 for p in ROOT.glob('*.py'):compile(p.read_bytes(),str(p),'exec')
 subprocess.run([sys.executable,'-B','-m','unittest','test_dashboard','test_coverage','-q'],cwd=ROOT,check=True)
 if args.check_only:print('CHECK PASS: dashboard-only patch; nothing changed.');return
 backup=Path('/root/kalpa-dashboard-v15-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(mode=0o700)
 for name,data in original.items():(backup/name).write_bytes(data)
 changed=[]
 try:
  for name,data in original.items():
   p=TARGET/name
   if p.read_bytes()!=data:raise RuntimeError('Concurrent edit detected: '+name)
   atomic(p,(ROOT/name).read_bytes(),modes[name]);changed.append(name)
 except Exception:
  for name in changed:
   p=TARGET/name
   if p.read_bytes()==(ROOT/name).read_bytes():atomic(p,original[name],modes[name])
  raise
 print('Dashboard v1.5 installed. Backup:',backup)
 print('Central Sync, databases and archives were not modified or restarted.')
 print('Close existing dashboard with Ctrl+C, then run: kalpa-delivery-dashboard')
 print('Existing background viewers keep their loaded version until restarted.')
if __name__=='__main__':
 try:main()
 except Exception as exc:print('ERROR:',type(exc).__name__,str(exc),file=sys.stderr);sys.exit(1)
