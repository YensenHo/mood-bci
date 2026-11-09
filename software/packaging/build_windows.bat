@echo off
REM ============================================================
REM  eegscope 打包脚本（Windows）
REM  作用：建隔离 venv → 装依赖 + pyinstaller → 打包 → 打 zip
REM  用法：双击本文件，或命令行运行 packaging\build_windows.bat
REM  产物：dist\eegscope-windows.zip（解压后双击 eegscope.exe 即可跑）
REM ============================================================
cd /d "%~dp0\.."

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 没找到 Python。请先装 Python 3.10+（勾选 "Add python.exe to PATH"）。
    echo 下载: https://www.python.org/downloads/
    pause
    exit /b 1
)

if not exist .build_venv (
    echo [1/4] 创建打包用虚拟环境 .build_venv ...
    python -m venv .build_venv
)

set PY=.build_venv\Scripts\python.exe

echo [2/4] 安装依赖 + pyinstaller（清华镜像）...
%PY% -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
%PY% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
%PY% -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple

echo [3/4] PyInstaller 打包 ...
%PY% -m PyInstaller --noconfirm --clean packaging\eegscope.spec

echo [4/4] 打 zip ...
if exist dist\eegscope-windows.zip del dist\eegscope-windows.zip
powershell -NoProfile -Command "Compress-Archive -Path dist\eegscope -DestinationPath dist\eegscope-windows.zip -Force"

echo.
echo ============================================================
echo   打包完成：dist\eegscope-windows.zip
echo   解压后运行 dist\eegscope\eegscope.exe
echo ============================================================
pause
