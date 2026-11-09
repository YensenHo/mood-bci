# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir，单文件夹，启动快、config 可直接改）。

用法（在 software/ 下）：
    pyinstaller packaging/eegscope.spec

产物：dist/eegscope/ 目录，内含 eegscope(.exe) + config*.yaml + drivers/ + scripts/。
    打包成 zip 即可「直接下载 → 解压 → 双击运行」，无需装 Python。
"""
import os

# SPECPATH = 本 .spec 所在目录（packaging/）；上一级 = software/（源码根）
SRC = os.path.dirname(SPECPATH)

datas = []

# 1) 配置文件（打包进产物根，用户可改 ports）
for name in ("config.yaml", "config64.yaml", "config_remote.yaml"):
    p = os.path.join(SRC, name)
    if os.path.exists(p):
        datas.append((p, "."))

# 2) STM32 VCP 驱动安装器（若已放到 software/drivers/，打包后 exe 能一键装）
_drv = os.path.join(SRC, "drivers")
if os.path.isdir(_drv):
    for f in sorted(os.listdir(_drv)):
        if f.lower().endswith((".exe", ".zip")):
            datas.append((os.path.join(_drv, f), "drivers"))

# 3) 扫描串口 / 摸电极识板等脚本（打包版里「高级」按钮进程内跑）
_scripts = os.path.join(SRC, "scripts")
if os.path.isdir(_scripts):
    for f in sorted(os.listdir(_scripts)):
        if f.endswith(".py"):
            datas.append((os.path.join(_scripts, f), "scripts"))

a = Analysis(
    [os.path.join(SRC, "entry.py")],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # pyqtgraph 部分子模块靠运行时 import，PyInstaller 静态分析可能漏掉
        "pyqtgraph.graphicsItems",
        "pyqtgraph.graphicsItems.PlotItem",
        "pyqtgraph.exporters",
        # scipy 的 fft/signal/optimize 走运行时加载
        "scipy.signal",
        "scipy.fft",
        "scipy.optimize",
        "scipy._lib.messagestream",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="eegscope",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 图形程序：Windows 不弹黑框，macOS 无终端
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="eegscope",
)
