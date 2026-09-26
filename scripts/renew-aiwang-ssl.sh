#!/usr/bin/env bash
# 在 DNSPod 解除域名拦截、HTTP-01 能访问本机后再执行。
set -euo pipefail
sudo mkdir -p /var/www/certbot/.well-known/acme-challenge
sudo /snap/bin/certbot certonly --webroot -w /var/www/certbot \
  -d www.aiwang.cloud -d aiwang.cloud \
  --force-renewal --non-interactive --agree-tos --register-unsafely-without-email
sudo nginx -t && sudo systemctl reload nginx
echo "证书已更新。请访问 https://www.aiwang.cloud/login"
