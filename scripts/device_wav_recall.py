#!/usr/bin/env python3
"""Real-time WAV recall checkpoints; records logs/screenshots without touching other apps."""
import argparse,json,os,pathlib,subprocess,threading,time,wave,shutil
p=argparse.ArgumentParser();p.add_argument('--adb',default=os.environ.get('ADB') or shutil.which('adb') or str(pathlib.Path.home()/'Library/Android/sdk/platform-tools/adb'));p.add_argument('--output',default='evidence/device/timed-recall');p.add_argument('--include-expiry',action='store_true');a=p.parse_args()
out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=True)
def adb(*args,check=True):return subprocess.run([a.adb,*args],capture_output=True,check=check)
adb('shell','am','start','-n','com.oops.app/.MainActivity','-a','com.oops.app.action.DEBUG_STOP');time.sleep(1)
log=subprocess.Popen([a.adb,'logcat','-T','1','-v','threadtime','-s','Oops','OopsBridge','OopsWeb','AndroidRuntime'],stdout=subprocess.PIPE,text=True)
lines=[]
def collect():
 with (out/'logcat.txt').open('w') as f:
  for line in log.stdout:lines.append(line);f.write(line);f.flush()
reader=threading.Thread(target=collect,daemon=True);reader.start()
start=time.monotonic()
adb('shell','am','start','-n','com.oops.app/.MainActivity','-a','com.oops.app.action.DEBUG_START','--es','source','wav','--ez','builtin','true')
with wave.open('android/app/src/main/assets/test/dialog.wav') as wav:duration=wav.getnframes()/wav.getframerate()
checkpoints=[(duration+d,str(d)) for d in [10,30,60,120]]
if a.include_expiry:checkpoints.append((duration+305,'expired'))
try:
 for target,label in checkpoints:
  while time.monotonic()-start<target:time.sleep(min(0.5,target-(time.monotonic()-start)))
  before=len(lines);trigger_at=time.monotonic()
  adb('shell','am','start','-n','com.oops.app/.MainActivity','-a','com.oops.app.action.RECALL','--es','query','注册前应该先让用户体验一下产品')
  deadline=time.monotonic()+8
  while time.monotonic()<deadline and not any('recall c' in s for s in lines[before:]):time.sleep(0.1)
  (out/f'{label}s.png').write_bytes(adb('exec-out','screencap','-p').stdout)
  matches=[s.strip() for s in lines[before:] if 'recall c' in s]
  record={'checkpoint':label,'after_wav_seconds':round(trigger_at-start-duration,3),'recall_log':matches,'final_count':sum('final u' in s for s in lines)}
  with (out/'checkpoints.jsonl').open('a') as f:f.write(json.dumps(record,ensure_ascii=False)+'\n')
  print(json.dumps(record,ensure_ascii=False),flush=True)
finally:
 adb('shell','am','start','-n','com.oops.app/.MainActivity','-a','com.oops.app.action.DEBUG_STOP',check=False)
 log.terminate();reader.join(timeout=2)
