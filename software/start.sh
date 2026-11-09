#!/usr/bin/env bash
# ============================================================
#  eegscope 一键启动（macOS / Linux）
#  作用：建虚拟环境 → 装依赖（缺了才装）→ 打开启动器
#  用法：bash start.sh
# ============================================================
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "[错误] 没找到 python3，请先安装 Python 3.10+："
    echo "       https://www.python.org/downloads/"
    exit 1
fi

if [ ! -d .venv ]; then
    echo "[1/3] 首次运行：创建虚拟环境 .venv ..."
    python3 -m venv .venv
fi

PY=".venv/bin/python"
[ -x "$PY" ] || PY=".venv/bin/python3"

# 快速依赖检查：能 import 就跳过安装，省得每次启动都联网
if ! "$PY" -c "import PySide6, pyqtgraph, numpy, scipy, serial, yaml" >/dev/null 2>&1; then
    echo "[2/3] 安装依赖（清华镜像，首次要几分钟）..."
    "$PY" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple >/dev/null
    "$PY" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
else
    echo "[2/3] 依赖已装齐，跳过。"
fi

echo "[3/3] 打开启动器..."
exec "$PY" launcher.py
