#!/usr/bin/env python3
"""30-minute screen-off WAV run. Network interruption is optional and explicitly enabled."""
import argparse,json,os,pathlib,re,shutil,subprocess,threading,time,hashlib,datetime
p=argparse.ArgumentParser();p.add_argument('--adb',default=os.environ.get('ADB') or shutil.which('adb') or str(pathlib.Path.home()/'Library/Android/sdk/platform-tools/adb'));p.add_argument('--duration',type=int,default=1800);p.add_argument('--wav-path',required=True);p.add_argument('--interrupt-network',action='store_true');p.add_argument('--output',default='evidence/device/stability');a=p.parse_args()
if a.duration<60: p.error('duration must be at least 60 seconds')
out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=True)
def adb(*args,check=True):return subprocess.run([a.adb,*args],capture_output=True,check=check,timeout=30)
def read(*args):return adb(*args).stdout.decode(errors='replace')
original_airplane=read('shell','settings','get','global','airplane_mode_on').strip()
if a.interrupt_network and original_airplane!='0':raise SystemExit('Airplane mode is already active or unknown; not changing it')
metadata={'duration_s':a.duration,'wav_path':a.wav_path,'device':read('shell','getprop','ro.product.model').strip(),'fingerprint':read('shell','getprop','ro.build.fingerprint').strip(),'original_airplane':original_airplane,'apk_sha256':hashlib.sha256(pathlib.Path('android/app/build/outputs/apk/debug/app-debug.apk').read_bytes()).hexdigest()}
(out/'metadata.json').write_text(json.dumps(metadata,indent=2))
adb('shell','am','start','-n','com.oops.app/.MainActivity','-a','com.oops.app.action.DEBUG_STOP');time.sleep(1)
log=subprocess.Popen([a.adb,'logcat','-T','1','-v','threadtime','-s','Oops','OopsBridge','OopsWeb','AndroidRuntime'],stdout=subprocess.PIPE,text=True)
lines=[]
def collect():
 with (out/'logcat.txt').open('w') as f:
  for line in log.stdout:lines.append(line);f.write(line);f.flush()
reader=threading.Thread(target=collect,daemon=True);reader.start();network_changed=False;started=False
try:
 adb('shell','am','start','-n','com.oops.app/.MainActivity','-a','com.oops.app.action.DEBUG_START','--es','source','wav','--es','wav_path',a.wav_path);started=True
 start=time.monotonic();next_sample=0;screen_off=False;interrupted=False;restored=False
 while time.monotonic()-start<a.duration:
  elapsed=time.monotonic()-start
  if any('capture failed to start' in l or 'FATAL EXCEPTION' in l for l in lines):
   raise RuntimeError('Capture failed; see logcat.txt. Stability run did not start')
  if elapsed>=20 and not any('ASR connected' in l for l in lines):
   raise RuntimeError('ASR did not connect within 20 seconds; refusing to record a silent pass')
  if elapsed>=30 and not screen_off:
   adb('shell','input','keyevent','223');screen_off=True
  if a.interrupt_network and elapsed>=600 and not interrupted:
   adb('shell','cmd','connectivity','airplane-mode','enable');network_changed=True;interrupted=True
   (out/'network-off.txt').write_text(read('shell','settings','get','global','airplane_mode_on'));print('Network interruption started',flush=True)
  if network_changed and elapsed>=630 and not restored:
   adb('shell','cmd','connectivity','airplane-mode','disable');network_changed=False;restored=True
   (out/'network-restored.txt').write_text(read('shell','settings','get','global','airplane_mode_on'));print('Network restored after 30 seconds',flush=True)
  if elapsed>=next_sample:
   mem=read('shell','dumpsys','meminfo','com.oops.app');services=read('shell','dumpsys','activity','services','com.oops.app');power=read('shell','dumpsys','power')
   (out/f'mem-{int(next_sample):04}.txt').write_text(mem);(out/f'service-{int(next_sample):04}.txt').write_text(services);(out/f'power-{int(next_sample):04}.txt').write_text(power)
   match=re.search(r'TOTAL PSS:\s*(\d+)',mem) or re.search(r'\bTOTAL\s+(\d+)',mem)
   row={'elapsed_s':round(elapsed,2),'pss_kb':int(match.group(1)) if match else None,'final_count':sum('final u' in l for l in lines),'prune_count':sum('pruned ' in l for l in lines),'connected_count':sum('ASR connected' in l for l in lines),'crash':any('FATAL EXCEPTION' in l for l in lines),'foreground_microphone':'isForeground=true' in services and ('foregroundServiceType=0x00000080' in services or 'foregroundServiceType=128' in services or 'types=0x00000080' in services),'interactive':re.findall(r'mWakefulness=\w+|mInteractive=\w+',power)}
   with (out/'samples.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
   print(json.dumps(row),flush=True);next_sample+=60
  time.sleep(0.25)
 adb('shell','input','keyevent','224');adb('shell','wm','dismiss-keyguard',check=False)
 adb('shell','am','start','-n','com.oops.app/.MainActivity')
 time.sleep(1);(out/'end.png').write_bytes(adb('exec-out','screencap','-p').stdout)
 (out/'completion.json').write_text(json.dumps({'elapsed_seconds':round(time.monotonic()-start,2),'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}))
 print('Screen-off stability run finished',flush=True)
finally:
 if network_changed:adb('shell','cmd','connectivity','airplane-mode','disable',check=False)
 if started:adb('shell','am','start','-n','com.oops.app/.MainActivity','-a','com.oops.app.action.DEBUG_STOP',check=False)
 log.terminate();reader.join(timeout=2)
