#!/usr/bin/env python3
"""check_env.py —— 一键环境自检（傻子化体检报告）。

检查 4 件事，每件给「✅ 通过 / ⚠️ 缺啥」+ 大白话修复建议：
  1. Python 版本（要 3.10+）
  2. 依赖包（PySide6 / pyqtgraph / numpy / scipy / pyserial / PyYAML）
  3. STM32 虚拟串口（板子 + 驱动是否就绪）
  4. 最后给一句总结 + 下一步该干嘛

如果 STM32 串口没检测到，Windows 下会提示装 STM32 VCP 驱动，并可一键打开下载页。

用法：
  Windows:  .venv\\Scripts\\python scripts\\check_env.py [--open-driver]
  macOS:    .venv/bin/python scripts/check_env.py [--open-driver]
"""
from __future__ import annotations

import os
import platform
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ST 官方 STM32 虚拟串口(VCP)驱动页面（Windows 必装）
STM32_DRIVER_URL = "https://www.st.com/en/development-tools/stsw-stm32102.html"

_DEPS = {
    "PySide6": "pip install PySide6",
    "pyqtgraph": "pip install pyqtgraph",
    "numpy": "pip install numpy",
    "scipy": "pip install scipy",
    "serial": "pip install pyserial",
    "yaml": "pip install PyYAML",
}


def check_python() -> tuple[bool, str]:
    ok = sys.version_info >= (3, 10)
    return ok, platform.python_version()


def check_deps() -> tuple[bool, list]:
    missing = []
    for mod, hint in _DEPS.items():
        try:
            __import__(mod)
        except Exception:
            missing.append((mod, hint))
    return not missing, missing


def check_stm32() -> tuple[bool, list]:
    try:
        from eegscope.sources.ports import detect_stm32_ports
        ports = detect_stm32_ports()
    except Exception:
        ports = []
    return len(ports) > 0, ports


def main() -> int:
    open_driver = "--open-driver" in sys.argv
    is_win = sys.platform.startswith("win")
    is_mac = sys.platform.startswith("darwin")

    print("=" * 60)
    print("  eegscope 环境自检")
    print("=" * 60)

    # 1) Python
    ok_py, py_ver = check_python()
    print(f"\n[1/4] Python 版本")
    if ok_py:
        print(f"  ✅ 通过：{py_ver}")
    else:
        print(f"  ❌ 版本太旧：{py_ver}（要 3.10+）")
        print("     去 https://www.python.org/downloads/ 装新版，勾选 Add to PATH")

    # 2) 依赖
    ok_dep, missing = check_deps()
    print(f"\n[2/4] 依赖包")
    if ok_dep:
        print("  ✅ 全部装齐")
    else:
        print(f"  ❌ 缺 {len(missing)} 个：")
        for mod, hint in missing:
            print(f"     - {mod}  →  {hint}")
        print("     批量装：pip install -r requirements.txt")

    # 3) STM32 串口
    ok_stm, ports = check_stm32()
    print(f"\n[3/4] STM32 采集板")
    if ok_stm:
        print(f"  ✅ 检测到 {len(ports)} 块板：")
        for p in ports:
            sn = getattr(p, "serial_number", None) or "?"
            print(f"     - {p.device}  (SN={sn})")
    else:
        print("  ⚠️ 没检测到 STM32 串口。分两种情况：")
        if is_win:
            print("     a) 板子没插/没开机 → 检查 USB 线、按蓝键开机")
            print("     b) 没装 STM32 驱动 → Windows 默认不认 STM32 虚拟串口")
            print(f"        驱动下载页：{STM32_DRIVER_URL}")
            print("        装完驱动，板子会出现在设备管理器「端口」里变成 COM3/COM4…")
        elif is_mac:
            print("     a) 板子没插/没开机 → 检查 USB 线、按蓝键开机")
            print("     b) Type-C 直连 Mac 不识别 → 必须经 USB-A 侧（转接头/集线器）")
        else:
            print("     a) 板子没插/没开机 → 检查 USB 线、按蓝键开机")
            print("     b) 权限不足 → sudo usermod -aG dialout $USER 后重插")

    # 4) 总结
    print("\n" + "=" * 60)
    all_ok = ok_py and ok_dep and ok_stm
    if all_ok:
        print("  ✅ 环境全部就绪，可以直接跑：")
        if is_win:
            print("     .venv\\Scripts\\python main.py config64.yaml")
        else:
            print("     .venv/bin/python main.py config64.yaml")
        return 0

    print("  ⚠️ 环境没就绪，按上面 [1][2][3] 的提示修完再跑。")
    if not ok_stm and is_win:
        if open_driver or (input("\n要现在打开 STM32 驱动下载页吗？(y/n): ").strip().lower() == "y"):
            print("  正在打开浏览器…")
            webbrowser.open(STM32_DRIVER_URL)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
