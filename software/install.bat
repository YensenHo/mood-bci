@echo off
REM ============================================================
REM  eegscope Windows 安装脚本：创建虚拟环境并安装依赖
REM  用法：双击本文件，或在命令行运行 install.bat
REM ============================================================
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 没找到 Python。请先安装 Python 3.10+（安装时勾选 "Add python.exe to PATH"）。
    echo 下载: https://www.python.org/downloads/
    echo.
    echo 另：本程序读 STM32 虚拟串口，Windows 上还需装 STM32 VCP 驱动（见 README）。
    pause
    exit /b 1
)

if not exist .venv (
    echo [1/2] 创建虚拟环境 .venv ...
    python -m venv .venv
)

echo [2/2] 安装依赖（清华镜像加速）...
.venv\Scripts\python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv\Scripts\python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

echo.
echo ============================================================
echo  完成！先跑环境自检：
echo    .venv\Scripts\python scripts\check_env.py
echo  然后运行：
echo    单板/16通道:  .venv\Scripts\python main.py config.yaml
echo    64通道:       .venv\Scripts\python main.py config64.yaml
echo    扫串口:       .venv\Scripts\python scripts\detect_boards.py
echo ============================================================
pause
