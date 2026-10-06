#!/bin/sh
set -eu
BASE=${1:?用法: scripts/api_smoke.sh http://host:port}
BASE=${BASE%/}
TASK_DIR=$(mktemp -d)
trap 'rm -rf "$TASK_DIR"' EXIT
export OOPS_SMOKE_DIR="$TASK_DIR"
python3 - <<'PY'
import os,json,uuid,datetime,pathlib
p=pathlib.Path(os.environ['OOPS_SMOKE_DIR'])
c={'id':'u-smoke','start_ms':5000,'end_ms':9000,'speaker':None,'text':'Jason 明天把方案发给你。'}
reference=datetime.datetime.now(datetime.timezone.utc).isoformat()
requests={'recall':{'session_id':'smoke','trigger':'button','query':'把刚才那个方案留下来','now_ms':12000,'candidates':[c]},'draft':{'session_id':'smoke','reference_time':reference,'time_zone':'Asia/Shanghai','utterances':[c],'context':[]},'confirm':{'op_id':str(uuid.uuid4()),'session_id':'smoke','draft':{'who':'Jason','what':'[Oops API smoke] 明天发送方案','when':None,'constraint':None},'evidence':{'utterance_ids':[c['id']],'quote':c['text'],'clip_start_ms':c['start_ms'],'clip_end_ms':c['end_ms']}}}
for name,body in requests.items(): (p/(name+'.json')).write_text(json.dumps(body,ensure_ascii=False))
PY
curl --fail-with-body --silent --show-error --max-time 10 "$BASE/v1/recall" -H 'Content-Type: application/json' --data-binary "@$TASK_DIR/recall.json" > "$TASK_DIR/recall-response.json"
curl --fail-with-body --silent --show-error --max-time 10 "$BASE/v1/actions/draft" -H 'Content-Type: application/json' --data-binary "@$TASK_DIR/draft.json" > "$TASK_DIR/draft-response.json"
python3 - <<'PY'
import os,json,pathlib
p=pathlib.Path(os.environ['OOPS_SMOKE_DIR'])
r=json.loads((p/'recall-response.json').read_text());assert isinstance(r['picks'],list);assert isinstance(r['engine'],str)
for item in r['picks']: assert item['id']=='u-smoke' and isinstance(item['prob'],(int,float)) and 0<=item['prob']<=1
r=json.loads((p/'draft-response.json').read_text());assert isinstance(r['what'],str) and r['what'];assert 'who' in r and 'when' in r
print('recall / draft: 字段与候选编号有效')
PY
if [ "${OOPS_SMOKE_CONFIRM:-0}" != 1 ]; then
  echo '未执行写入；测试环境确认后以 OOPS_SMOKE_CONFIRM=1 运行完整四接口检查。'
  exit 0
fi
for attempt in 1 2; do
  curl --fail-with-body --silent --show-error --max-time 10 "$BASE/v1/actions/confirm" -H 'Content-Type: application/json' --data-binary "@$TASK_DIR/confirm.json" > "$TASK_DIR/confirm-$attempt.json"
done
OP_ID=$(python3 -c 'import json,os;print(json.load(open(os.environ["OOPS_SMOKE_DIR"]+"/confirm.json"))["op_id"])')
for poll in 1 2 3 4 5 6 7 8 9 10; do
  curl --fail-with-body --silent --show-error --max-time 10 "$BASE/v1/actions/$OP_ID" > "$TASK_DIR/status.json"
  STATUS=$(python3 -c 'import json,os;print(json.load(open(os.environ["OOPS_SMOKE_DIR"]+"/status.json"))["status"])')
  case "$STATUS" in created|failed) break ;; pending) sleep 1 ;; *) echo '未知状态'; exit 2 ;; esac
done
python3 - <<'PY'
import os,json,pathlib
p=pathlib.Path(os.environ['OOPS_SMOKE_DIR']);expected=json.loads((p/'confirm.json').read_text())['op_id']
results=[json.loads((p/name).read_text()) for name in ['confirm-1.json','confirm-2.json','status.json']]
for r in results: assert r['op_id']==expected and r['status'] in ['pending','created','failed']
guids={r['feishu_task']['guid'] for r in results if r['status']=='created'}
assert len(guids)<=1, '同 op_id 出现了不同任务'
print('confirm / status: 编号一致，重复确认没有返回不同任务；最终状态',results[-1]['status'])
if results[-1]['status']!='created': raise SystemExit('任务未达到 created；行动闭环检查未通过。')
PY
