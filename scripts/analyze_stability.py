#!/usr/bin/env python3
"""Summarize an observed run; raw results remain available and do not imply human acceptance."""
import argparse,json,pathlib,re,statistics
p=argparse.ArgumentParser();p.add_argument('directory');a=p.parse_args();directory=pathlib.Path(a.directory)
samples=[json.loads(s) for s in (directory/'samples.jsonl').read_text().splitlines() if s]
lines=(directory/'logcat.txt').read_text().splitlines();warm=[s for s in samples if s['elapsed_s']>=300 and s['pss_kb'] is not None]
if len(warm)>1:
 xs=[s['elapsed_s']/60 for s in warm];ys=[s['pss_kb']/1024 for s in warm];avgx=statistics.mean(xs);avgy=statistics.mean(ys)
 slope=sum((x-avgx)*(y-avgy) for x,y in zip(xs,ys))/sum((x-avgx)**2 for x in xs)
 memory={'warmup_seconds':300,'min_mib':round(min(ys),2),'max_mib':round(max(ys),2),'last_mib':round(ys[-1],2),'slope_mib_per_minute':round(slope,3)}
else:memory=None
screen_off_memory=[s['pss_kb']/1024 for s in warm if any('Dozing' in x or 'Asleep' in x for x in s['interactive'])]
if memory is not None and screen_off_memory:
 memory['screen_off_min_mib']=round(min(screen_off_memory),2)
 memory['screen_off_max_mib']=round(max(screen_off_memory),2)
 memory['screen_off_sample_count']=len(screen_off_memory)
service_checks=[]
for file in sorted(directory.glob('service-*.txt')):
 raw=file.read_text();service_checks.append({'file':file.name,'foreground_microphone':'isForeground=true' in raw and ('types=0x00000080' in raw or 'foregroundServiceType=0x00000080' in raw)})
summary={'metadata':json.loads((directory/'metadata.json').read_text()),'last_sample_seconds':samples[-1]['elapsed_s'],'sample_count':len(samples),'memory':memory,'final_count':sum('final u' in s for s in lines),'prune_events':sum('pruned ' in s for s in lines),'connections':sum('ASR connected' in s for s in lines),'disconnects':[s for s in lines if 'ASR closed' in s],'crash':any('FATAL EXCEPTION' in s for s in lines),'screen_off_sample_seconds':[s['elapsed_s'] for s in samples if any('Dozing' in x or 'Asleep' in x for x in s['interactive'])],'foreground_microphone_samples':sum(s['foreground_microphone'] for s in service_checks),'service_sample_count':len(service_checks),'network_off_value':(directory/'network-off.txt').read_text().strip() if (directory/'network-off.txt').exists() else None,'network_restored_value':(directory/'network-restored.txt').read_text().strip() if (directory/'network-restored.txt').exists() else None,'scope':'Technical test audio pipeline; microphone human speech and listening acceptance are separate'}
(directory/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False,indent=2))
