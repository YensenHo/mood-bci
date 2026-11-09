"""环境自检 + 一键修复（launcher GUI 与 scripts/check_env.py 共用）。

检查 4 件事，每件给「通过 / 缺啥」+ 大白话说明：
  1. Python 版本（要 3.10+）
  2. 依赖包（PySide6 / pyqtgraph / numpy / scipy / pyserial / PyYAML）
  3. STM32 采集板（USB 虚拟串口是否就绪）
  4. STM32 驱动（仅 Windows 需要；macOS / Linux 系统自带 CDC）

修复动作：
  - install_deps()     用 pip 装缺失依赖（清华镜像加速）
  - install_driver()   运行 STM32 VCP 驱动安装器（software/drivers/ 里放好的 exe）
"""
from __future__ import annotations

import glob
import os
import platform
import subprocess
import sys

from eegscope import _runtime

SOFTWARE_DIR = _runtime.resource_root()  # 只读资源根（dev=software/，frozen=_MEIPASS）
REQUIREMENTS = os.path.join(SOFTWARE_DIR, "requirements.txt")
PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"

# ST 官方 STM32 虚拟串口(VCP)驱动页面（Windows 必装；官网下载需登录，故优先走本地 drivers/）
STM32_DRIVER_URL = "https://www.st.com/en/development-tools/stsw-stm32102.html"

# (import 名, pip 包名)。import 名与包名不一致的（serial/pyserial、yaml/PyYAML）分开写。
_DEPS = [
    ("PySide6", "PySide6"),
    ("pyqtgraph", "pyqtgraph"),
    ("numpy", "numpy"),
    ("scipy", "scipy"),
    ("serial", "pyserial"),
    ("yaml", "PyYAML"),
]

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform.startswith("darwin")


def check_python():
    ok = sys.version_info >= (3, 10)
    return ok, platform.python_version()


def check_deps():
    missing = []
    for mod, pipname in _DEPS:
        try:
            __import__(mod)
        except Exception:
            missing.append((mod, pipname))
    return not missing, missing


def check_stm32_board():
    try:
        from eegscope.sources.ports import detect_stm32_ports
        ports = detect_stm32_ports()
    except Exception:
        ports = []
    return len(ports) > 0, ports


def _driver_store_has_stm32():
    """Windows 下看驱动库是否已装 STM32 VCP 驱动（不依赖板子此刻是否插着）。

    驱动装好后会在 C:\\Windows\\System32\\DriverStore\\FileRepository 下留下
    stm32... 开头的目录。非 Windows 返回 None（无此概念）。
    """
    if not IS_WIN:
        return None
    root = os.environ.get("SystemRoot", r"C:\Windows")
    repo = os.path.join(root, "System32", "DriverStore", "FileRepository")
    try:
        for name in os.listdir(repo):
            if name.lower().startswith("stm32"):
                return True
    except OSError:
        pass
    return False


def check_stm32_driver():
    """返回 (ok, note)。ok=True 表示驱动层面没问题。"""
    board_ok, ports = check_stm32_board()
    if board_ok:
        return True, f"已就绪（检测到 {len(ports)} 块板）"
    if not IS_WIN:
        return True, "免安装（macOS / Linux 系统自带 CDC 驱动）"
    # Windows 且没检测到板子：区分「没插板」vs「没装驱动」
    if _driver_store_has_stm32():
        return True, "驱动已装，但没检测到板子（可能没插 / 没开机）"
    return False, "Windows 未检测到 STM32 驱动（板子插了也不认，需安装）"


def find_bundled_driver():
    """找 STM32 VCP 驱动安装器（*.exe，Windows）。

    dev 模式：software/drivers/；frozen 模式：exe 目录/drivers/ 或打包资源/drivers/。
    """
    for d in _runtime.drivers_dirs():
        if not os.path.isdir(d):
            continue
        for p in sorted(glob.glob(os.path.join(d, "*.exe"))):
            return p
    return None


def run_all_checks():
    return {
        "python": check_python(),
        "deps": check_deps(),
        "board": check_stm32_board(),
        "driver": check_stm32_driver(),
    }


def install_deps(log=None):
    """pip 装依赖（清华镜像）。log 为逐行回调。返回 (ok, output)。"""
    cmd = [sys.executable, "-m", "pip", "install", "-r", REQUIREMENTS,
           "-i", PIP_INDEX]
    return _run(cmd, log)


def install_driver(exe_path, log=None):
    """运行驱动安装器。返回 (ok, output)。"""
    if not os.path.isfile(exe_path):
        return False, f"找不到安装器：{exe_path}"
    if IS_WIN and exe_path.lower().endswith(".exe"):
        startfile = getattr(os, "startfile", None)  # Windows-only API
        try:
            if startfile is not None:
                startfile(exe_path)  # Windows 默认方式启动（会弹 UAC + 安装向导）
            else:
                subprocess.Popen([exe_path])
        except Exception:
            subprocess.Popen([exe_path])
        msg = ("已启动安装程序，请在弹出的向导里一路点「下一步 / Install」装完，"
               "然后点「重新检测」。")
        if log:
            log(msg)
        return True, msg
    return False, "当前系统无需 STM32 驱动（macOS / Linux 系统自带 CDC）。"


def run_fix(log=None):
    """一键修复：装缺失依赖 +（Windows 缺驱动时）装驱动。返回 run_all_checks() 结果。"""
    def say(s):
        if log:
            log(s)

    ok_dep, missing = check_deps()
    if missing:
        names = "、".join(pip for _, pip in missing)
        say(f"检测到缺依赖：{names}，开始安装（清华镜像，可能要几分钟）…")
        ok, out = install_deps(say)
        if ok:
            say("依赖装好了。")
        else:
            say("⚠️ 依赖没装成功，请手动运行 install.sh（Mac/Linux）或 install.bat（Windows）。")
    else:
        say("依赖已齐全，跳过。")

    if IS_WIN:
        ok_drv, drv_note = check_stm32_driver()
        if not ok_drv:
            exe = find_bundled_driver()
            if exe:
                say(f"找到本地驱动安装器：{os.path.basename(exe)}")
                install_driver(exe, say)
            else:
                say("没找到本地驱动安装器（software/drivers/ 目录为空）。")
                say("请到 ST 官网下载 STM32 Virtual COM Port Driver，")
                say("把安装器 exe 放进 software/drivers/，再点一次「一键修复」。")
                say(f"官网：{STM32_DRIVER_URL}")
        else:
            say(f"STM32 驱动：{drv_note}")
    else:
        say("当前系统无需 STM32 驱动（系统自带）。")

    return run_all_checks()


def _run(cmd, log=None):
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=_win_no_window())
        out = []
        if proc.stdout is not None:
            for line in proc.stdout:
                line = line.rstrip()
                out.append(line)
                if log:
                    log(line)
        proc.wait()
        return proc.returncode == 0, "\n".join(out)
    except Exception as e:  # noqa: BLE001
        return False, f"执行失败：{e}"


def _win_no_window():
    """Windows 下隐藏 pip 弹出来的黑色控制台窗口；其它平台返回 0。"""
    if IS_WIN:
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0
