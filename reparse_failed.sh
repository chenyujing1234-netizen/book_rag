#!/usr/bin/env bash
# 重新解析 parse_status=failed 的文档。
#
# 背景：通过 API/MCP 创建的知识库 embedding_model_id 为空，导致文档解析到向量化
# 阶段拿不到模型，任务挂死后被 housekeeping 标记 failed。知识库配置已于
# 2026-09-16 补齐，源文件仍在卷里，因此只需重新解析、无需重新上传。
#
# 用法：
#   ./reparse_failed.sh --dry-run                 # 只列出将要处理的文档
#   ./reparse_failed.sh --types txt,doc           # 只跑 txt 和 doc
#   ./reparse_failed.sh --types pdf --max-size 5  # 只跑 5MB 以内的 pdf
#   ./reparse_failed.sh --limit 10                # 只跑前 10 篇
#
# 串行逐篇提交，每篇轮询到终态才继续；可用内存不足时自动等待。

set -uo pipefail

APP_URL="http://127.0.0.1:8083"
TYPES=""
MAX_SIZE_MB=""
LIMIT=""
DRY_RUN=0
POLL_TIMEOUT=900      # 单篇最长等待秒数
MIN_FREE_MB=350       # 可用内存低于此值则暂停提交
MIN_DISK_MB=1200      # 根分区可用空间低于此值则中止（大 PDF 解析会写临时文件）

while [[ $# -gt 0 ]]; do
  case "$1" in
    --types)    TYPES="$2"; shift 2 ;;
    --max-size) MAX_SIZE_MB="$2"; shift 2 ;;
    --limit)    LIMIT="$2"; shift 2 ;;
    --dry-run)  DRY_RUN=1; shift ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

psql_q() { docker exec WeKnora-postgres psql -U weknora -d weknora -t -A -F'|' -c "$1"; }

# 取剩余有效期最长的登录态
fresh_token() {
  psql_q "SELECT token FROM auth_tokens WHERE token_type='access_token' AND is_revoked=false AND expires_at>now() ORDER BY expires_at DESC LIMIT 1;"
}

USE_DB_TOKEN=0

# 认证：优先用环境变量里的 API Key，否则取数据库里未过期的登录态
if [[ -n "${WEKNORA_API_KEY:-}" ]]; then
  AUTH_HEADER="X-API-Key: $WEKNORA_API_KEY"
else
  # 登录态按剩余有效期取，不是按签发时间——库里常有更晚签发但已快过期的 token。
  # 2026-09-19 踩过：取错 token，28 篇跑到第 6 篇起全部 401。
  # tenant_api_keys 里的 api_key 是 enc:v1: 密文，取出来不能直接用，所以只能走登录态。
  TOKEN=$(fresh_token)
  if [[ -z "$TOKEN" ]]; then
    echo "没有可用的登录态。请在浏览器登录一次 WeKnora，或设置 WEKNORA_API_KEY 后重试。" >&2
    exit 1
  fi
  USE_DB_TOKEN=1
fi

WHERE="k.parse_status='failed' AND k.deleted_at IS NULL"
[[ -n "$TYPES" ]] && WHERE="$WHERE AND k.file_type IN ('${TYPES//,/\',\'}')"
[[ -n "$MAX_SIZE_MB" ]] && WHERE="$WHERE AND k.file_size <= $((MAX_SIZE_MB * 1024 * 1024))"
SQL="SELECT k.id, k.knowledge_base_id, k.file_type, k.file_size, kb.name, k.title
     FROM knowledges k JOIN knowledge_bases kb ON kb.id = k.knowledge_base_id
     WHERE $WHERE ORDER BY k.file_size"
[[ -n "$LIMIT" ]] && SQL="$SQL LIMIT $LIMIT"

mapfile -t ROWS < <(psql_q "$SQL;")
TOTAL=${#ROWS[@]}
echo "待处理 $TOTAL 篇"
[[ $TOTAL -eq 0 ]] && exit 0

if [[ $DRY_RUN -eq 1 ]]; then
  printf '%s\n' "${ROWS[@]}" | awk -F'|' '{printf "  %-5s %8.1fKB  [%s] %s\n", $3, $4/1024, $5, $6}'
  exit 0
fi

OK=0; BAD=0; I=0
for row in "${ROWS[@]}"; do
  IFS='|' read -r kid kbid ftype fsize kbname title <<<"$row"
  I=$((I + 1))
  printf '[%d/%d] %s (%s, %dKB, 库: %s)\n' "$I" "$TOTAL" "$title" "$ftype" "$((fsize / 1024))" "$kbname"

  # 磁盘保护：空间耗尽会导致 postgres 写入失败，比解析失败严重得多，直接中止
  disk_mb=$(df -m / | awk 'NR==2{print $4}')
  if [[ "$disk_mb" -lt "$MIN_DISK_MB" ]]; then
    echo "    磁盘可用 ${disk_mb}MB < ${MIN_DISK_MB}MB，中止（已处理 $((I - 1)) 篇）"
    break
  fi

  # 内存保护：docreader 解析大文件时峰值明显，内存紧张就先等
  while :; do
    free_mb=$(free -m | awk '/^Mem:/{print $7}')
    [[ "$free_mb" -ge "$MIN_FREE_MB" ]] && break
    echo "    可用内存 ${free_mb}MB < ${MIN_FREE_MB}MB，等待 30s"
    sleep 30
  done

  # 大部头单篇可能跑十几分钟，登录态会在中途失效，所以每篇都重新取一次
  if [[ $USE_DB_TOKEN -eq 1 ]]; then
    TOKEN=$(fresh_token)
    if [[ -z "$TOKEN" ]]; then
      echo "    登录态已全部过期，请在浏览器登录一次后重跑" >&2
      break
    fi
    AUTH_HEADER="Authorization: Bearer $TOKEN"
  fi

  resp=$(curl -s -X POST -H "$AUTH_HEADER" -H 'Content-Type: application/json' \
    -d "{\"kb_id\":\"$kbid\",\"ids\":[\"$kid\"]}" \
    "$APP_URL/api/v1/knowledge/batch-reparse")
  if [[ "$resp" != *'"success":true'* ]]; then
    echo "    提交失败: ${resp:0:200}"
    BAD=$((BAD + 1)); continue
  fi

  waited=0
  while [[ $waited -lt $POLL_TIMEOUT ]]; do
    sleep 10; waited=$((waited + 10))
    st=$(psql_q "SELECT parse_status FROM knowledges WHERE id='$kid';")
    case "$st" in
      completed)
        n=$(psql_q "SELECT count(*) FROM chunks WHERE knowledge_id='$kid' AND deleted_at IS NULL;")
        echo "    完成，${n} 个分块（${waited}s）"
        OK=$((OK + 1)); break ;;
      failed)
        msg=$(psql_q "SELECT coalesce(error_message,'') FROM knowledges WHERE id='$kid';")
        echo "    失败: ${msg:0:160}"
        BAD=$((BAD + 1)); break ;;
    esac
  done
  if [[ $waited -ge $POLL_TIMEOUT ]]; then
    echo "    超过 ${POLL_TIMEOUT}s 仍未完成，跳过继续（后台可能仍在跑）"
    BAD=$((BAD + 1))
  fi
done

echo "----"
echo "成功 $OK 篇，未完成 $BAD 篇"
psql_q "SELECT parse_status||': '||count(*) FROM knowledges WHERE deleted_at IS NULL GROUP BY parse_status;"
