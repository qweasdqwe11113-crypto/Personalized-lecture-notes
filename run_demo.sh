#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'import flask, psycopg, dotenv' >/dev/null 2>&1; then
  echo '请先按 demo/README.md 安装依赖。'
  exit 1
fi
if [[ ! -f .env ]]; then
  echo '请复制 .env.example 为 .env 并填写模型密钥。'
  exit 1
fi
.venv/bin/python demo/init_db.py
exec .venv/bin/python demo/app.py
