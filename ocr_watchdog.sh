#!/bin/bash
# OCR 入库要跑十几个小时，中途进程可能因为网络或 OOM 退出。
# 这个脚本由 cron 每 10 分钟调一次：进程没在跑、任务又没完成，就重新拉起。
# ocr_ingest.py 每页结果都落盘，重启只会从断点继续，不会重复消耗 OCR 服务。
cd /home/chenyj/my_knowledge || exit 1

LOG=/tmp/ocr_all.log
STATE=ocr_cache/_uploaded.json
GUARD=ocr_cache/_restarts
MAX_RESTARTS=30          # 防止某页永久失败导致无限重启

pgrep -f 'ocr_ingest\.py' >/dev/null && exit 0

# 38 本全部处理完就收工
total=$(python3 - <<'EOF'
import json
print(len(json.load(open("/tmp/ocr_tasks.json"))))
EOF
)
done=$(python3 - <<'EOF'
import json, os
p = "ocr_cache/_uploaded.json"
print(len(json.load(open(p))) if os.path.exists(p) else 0)
EOF
)
if [[ "$done" -ge "$total" ]]; then
  echo "$(date '+%F %T') 全部 $done/$total 本已完成，watchdog 退出" >> "$LOG"
  crontab -l 2>/dev/null | grep -v ocr_watchdog | crontab -
  exit 0
fi

n=$(cat "$GUARD" 2>/dev/null || echo 0)
if [[ "$n" -ge "$MAX_RESTARTS" ]]; then
  echo "$(date '+%F %T') 已重启 $n 次仍未完成，停止自动重启，请人工检查" >> "$LOG"
  exit 1
fi
echo $((n + 1)) > "$GUARD"

echo "$(date '+%F %T') 检测到进程退出（$done/$total 本完成），第 $((n + 1)) 次重启" >> "$LOG"
setsid nohup python3 -u ocr_ingest.py >> "$LOG" 2>&1 < /dev/null &
