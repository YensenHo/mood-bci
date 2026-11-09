#!/usr/bin/env bash
# ============================================================
#  eegscope macOS / Linux 安装脚本：创建虚拟环境并安装依赖
#  用法：bash install.sh
# ============================================================
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "[错误] 没找到 python3，请先安装 Python 3.10+"
    exit 1
fi

if [ ! -d .venv ]; then
    echo "[1/2] 创建虚拟环境 .venv ..."
    python3 -m venv .venv
fi

echo "[2/2] 安装依赖（清华镜像加速）..."
.venv/bin/python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv/bin/python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

echo ""
echo "============================================================"
echo " 完成！推荐直接一键启动："
echo "   bash start.sh     （会打开图形启动器）"
echo ""
echo " 或手动："
echo "   自检:        .venv/bin/python scripts/check_env.py"
echo "   自检+修复:   .venv/bin/python scripts/check_env.py --fix"
echo "   单板/16通道:  .venv/bin/python main.py config.yaml"
echo "   64通道:       .venv/bin/python main.py config64.yaml"
echo "============================================================"
