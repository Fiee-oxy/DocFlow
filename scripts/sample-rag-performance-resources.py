"""Sample host/process resources during benchmark, without command lines/secrets."""
import argparse,json,time
from pathlib import Path
import psutil
a=argparse.ArgumentParser();a.add_argument('--run-dir',required=True);a.add_argument('--stop-file',default='no-llm-audit.json');a.add_argument('--output-file',default='resource-samples.jsonl');args=a.parse_args();run=Path(args.run_dir)
with (run/args.output_file).open('a',encoding='utf-8') as out:
 for _ in range(1440):
  rows=[]
  for proc in psutil.process_iter(['pid','name','memory_info','cpu_times']):
   try:
    info=proc.info;name=(info['name'] or '').lower()
    if any(x in name for x in ['java','python','docker','vmmem']):
     rows.append({'pid':info['pid'],'name':info['name'],'rss_bytes':info['memory_info'].rss,'cpu_seconds':sum(info['cpu_times'][:2])})
   except (psutil.NoSuchProcess,psutil.AccessDenied):pass
  mem=psutil.virtual_memory()
  out.write(json.dumps({'time':time.time(),'host_cpu_percent':psutil.cpu_percent(interval=1),'host_memory_used_bytes':mem.used,'host_memory_available_bytes':mem.available,'processes':rows})+'\n');out.flush()
  if (run/args.stop_file).exists():break
  time.sleep(4)
