@echo off
REM ============================================================
REM  eegscope 一键启动（Windows）
REM  作用：建虚拟环境 -> 装依赖（缺了才装）-> 打开启动器
REM  用法：双击本文件
REM ============================================================
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 没找到 Python。请先安装 Python 3.10+（安装时勾选 "Add python.exe to PATH"）。
    echo 下载: https://www.python.org/downloads/
    pause
    exit /b 1
)

if not exist .venv (
    echo [1/3] 首次运行：创建虚拟环境 .venv ...
    python -m venv .venv
)

set PY=.venv\Scripts\python.exe

%PY% -c "import PySide6, pyqtgraph, numpy, scipy, serial, yaml" >nul 2>nul
if errorlevel 1 (
    echo [2/3] 安装依赖（清华镜像，首次要几分钟）...
    %PY% -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
    %PY% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
) else (
    echo [2/3] 依赖已装齐，跳过。
)

echo [3/3] 打开启动器...
%PY% launcher.py
pause
