#!/usr/bin/env python3
"""Loopback-only HTTP contract fixture. It never contacts Jev, an LLM, or Feishu."""
import argparse,json,socket,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8766);p.add_argument('--scenario',choices=['created','drop-response-once','reject-once'],default='created');a=p.parse_args()
operations={};lock=threading.Lock()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def reply(self,value,code=200):
  payload=json.dumps(value,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
 def do_POST(self):
  try:
   size=int(self.headers.get('Content-Length','0'))
   if not 0<size<=1_000_000:raise ValueError('body size invalid')
   r=json.loads(self.rfile.read(size))
  except (ValueError,KeyError):return self.reply({'error':{'code':'bad_request','message':'无效测试请求'}},400)
  if self.path=='/v1/recall':
   picks=[{'id':r['candidates'][0]['id'],'prob':0.9}] if r['candidates'] else []
   return self.reply({'picks':picks,'engine':'local-fixture'})
  if self.path=='/v1/actions/draft':
   return self.reply({'who':None,'what':'[HTTP fixture] '+r['utterances'][0]['text'],'when':None,'constraint':None,'assignee_candidates':['Jason']})
  if self.path!='/v1/actions/confirm':return self.reply({'error':{'code':'not_found','message':'无此测试接口'}},404)
  op=r['op_id']
  with lock:
   item=operations.setdefault(op,{'request':r,'attempts':0,'created_count':0,'result':None})
   if item['request']!=r:return self.reply({'error':{'code':'idempotency_conflict','message':'同编号的确认内容变化'}},409)
   item['attempts']+=1;first=item['attempts']==1
   if first and a.scenario=='reject-once':return self.reply({'error':{'code':'unavailable','message':'本地测试：服务未接收该确认'}},503)
   if item['result'] is None:
    item['created_count']+=1
    item['result']={'op_id':op,'status':'created','feishu_task':{'guid':'fixture-'+op,'url':None},'message':'本地 HTTP 测试，没有写入飞书'}
   result=item['result']
  if first and a.scenario=='drop-response-once':
   self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
  self.reply(result)
 def do_GET(self):
  with lock:
   if self.path=='/_test/operations':return self.reply(operations)
   op=self.path.rsplit('/',1)[-1];item=operations.get(op)
   result=item['result'] if item else None
  self.reply(result or {'op_id':op,'status':'failed','feishu_task':None,'message':'本地测试服务未接收该操作，可用原编号重试'})
print('Local test fixture on 127.0.0.1:'+str(a.port)+'; scenario='+a.scenario,flush=True)
ThreadingHTTPServer(('127.0.0.1',a.port),Handler).serve_forever()
