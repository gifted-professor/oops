#!/usr/bin/env python3
"""Create validated streaming WAV copies and a manifest; preserve source files."""
import argparse,hashlib,json,pathlib,subprocess,wave,datetime
p=argparse.ArgumentParser();p.add_argument('sources',nargs='*');p.add_argument('--output',default='test-media');p.add_argument('--sample-seconds',type=int,default=180);a=p.parse_args()
sources=a.sources or ['/Volumes/Untitled/DCIM/DJI_002/DJI_20261005221824_0235_D.mp3','/Volumes/Untitled/DCIM/DJI_002/DJI_20261005232647_0238_D.mp3','/Volumes/Untitled/DCIM/DJI_002/DJI_20261005230143_0236_D.mp3']
out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=True)
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
records=[]
for source in sources:
 src=pathlib.Path(source)
 if not src.is_file():records.append({'source':str(src),'status':'missing'});continue
 before=digest(src);dst=out/(src.stem+'-16k-mono.wav')
 if not dst.exists():subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-n','-i',str(src),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(dst)],check=True)
 subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(dst),'-f','null','-'],check=True)
 with wave.open(str(dst)) as w:
  assert w.getnchannels()==1 and w.getframerate()==16000 and w.getsampwidth()==2
  seconds=w.getnframes()/16000
 assert digest(src)==before,'Source changed during conversion'
 sample=out/(src.stem+f'-first-{a.sample_seconds}s.wav')
 if not sample.exists():subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-n','-i',str(dst),'-t',str(a.sample_seconds),'-c:a','pcm_s16le',str(sample)],check=True)
 records.append({'source':str(src.resolve()),'source_sha256':before,'status':'converted','wav':str(dst.resolve()),'wav_sha256':digest(dst),'duration_seconds':seconds,'sample':str(sample.resolve()),'sample_selection':'first interval; content and reference annotations pending'})
manifest={'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'format':'PCM 16000 Hz / mono / 16 bit','recordings':records}
(out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(manifest,ensure_ascii=False,indent=2))
if not any(r['status']=='converted' for r in records):raise SystemExit(2)
