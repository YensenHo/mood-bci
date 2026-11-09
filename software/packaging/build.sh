#!/usr/bin/env bash
# ============================================================
#  eegscope 打包脚本（macOS / Linux）
#  作用：建隔离 venv → 装依赖 + pyinstaller → 打包 → 打 zip
#  用法：bash packaging/build.sh
#  产物：dist/eegscope-macos.zip（解压后双击 eegscope 即可跑）
# ============================================================
set -e
cd "$(dirname "$0")/.."   # 切到 software/

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

# 隔离环境，避免本机其它 Python 包的 PYTHONPATH 污染（见项目坑记录）
if [ -n "$VIRTUAL_ENV" ]; then
    echo "[提示] 已在 venv 中，直接用当前环境。"
else
    if [ ! -d .build_venv ]; then
        echo "[1/4] 创建打包用虚拟环境 .build_venv ..."
        $PY -m venv .build_venv
    fi
    source .build_venv/bin/activate
    PY=.build_venv/bin/python
fi

echo "[2/4] 安装依赖 + pyinstaller（清华镜像）..."
$PY -m pip install -q --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
$PY -m pip install -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
$PY -m pip install -q pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple

echo "[3/4] PyInstaller 打包 ..."
$PY -m PyInstaller --noconfirm --clean packaging/eegscope.spec

echo "[4/4] 打 zip ..."
PLATFORM=$(uname -s | tr '[:upper:]' '[:lower:]')
if [ "$PLATFORM" = "darwin" ]; then
    ZIP="dist/eegscope-macos.zip"
else
    ZIP="dist/eegscope-linux.zip"
fi
rm -f "$ZIP"
(cd dist && zip -rq "$(basename "$ZIP")" eegscope)

echo ""
echo "============================================================"
echo "  打包完成：$ZIP"
echo "  解压后运行 dist/eegscope/eegscope（首次启动自动建 config 副本）"
echo "============================================================"
