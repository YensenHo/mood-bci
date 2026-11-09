#!/usr/bin/env python3
"""check_env.py —— 一键环境自检（傻子化体检报告，命令行版）。

检查 4 件事，每件给「✅ 通过 / ⚠️ 缺啥」+ 大白话修复建议：
  1. Python 版本（要 3.10+）
  2. 依赖包（PySide6 / pyqtgraph / numpy / scipy / pyserial / PyYAML）
  3. STM32 采集板（板子 + 驱动是否就绪）
  4. STM32 虚拟串口（Windows 是否装 STM32 VCP 驱动）

参数：
  --fix             一键修复：自动装缺失依赖 +（Windows 缺驱动时）装 drivers/ 里的驱动
  --install-driver  直接运行 software/drivers/ 里的 STM32 驱动安装器
  --open-driver     打开 ST 官方驱动下载页

用法：
  Windows:  .venv\\Scripts\\python scripts\\check_env.py [--fix]
  macOS:    .venv/bin/python scripts/check_env.py [--fix]
"""
from __future__ import annotations

import os
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eegscope.envcheck import (STM32_DRIVER_URL, IS_WIN, find_bundled_driver,
                               install_driver, run_all_checks, run_fix)


def main() -> int:
    args = sys.argv[1:]

    if "--install-driver" in args:
        exe = find_bundled_driver()
        if not exe:
            print("❌ 没找到本地驱动安装器（software/drivers/ 目录为空）。")
            print(f"   请到官网下载后把 exe 放进 drivers/：{STM32_DRIVER_URL}")
            return 1
        ok, out = install_driver(exe, print)
        print(out)
        return 0 if ok else 1

    if "--fix" in args:
        print("=" * 60)
        print("  eegscope 一键修复")
        print("=" * 60)
        results = run_fix(print)
        print("\n" + "=" * 60)
        _print_summary(results)
        return 0 if _all_ready(results) else 1

    open_driver = "--open-driver" in args

    print("=" * 60)
    print("  eegscope 环境自检")
    print("=" * 60)

    results = run_all_checks()
    ok_py, py_ver = results["python"]
    ok_dep, missing = results["deps"]
    ok_board, ports = results["board"]
    ok_drv, drv_note = results["driver"]

    print("\n[1/4] Python 版本")
    if ok_py:
        print(f"  ✅ 通过：{py_ver}")
    else:
        print(f"  ❌ 版本太旧：{py_ver}（要 3.10+）")
        print("     去 https://www.python.org/downloads/ 装新版，勾选 Add to PATH")

    print("\n[2/4] 依赖包")
    if ok_dep:
        print("  ✅ 全部装齐")
    else:
        print(f"  ❌ 缺 {len(missing)} 个：")
        for mod, hint in missing:
            print(f"     - {mod}  →  pip install {hint}")
        print("     一键装：加 --fix 参数，或 pip install -r requirements.txt")

    print("\n[3/4] STM32 采集板")
    if ok_board:
        print(f"  ✅ 检测到 {len(ports)} 块板：")
        for p in ports:
            sn = getattr(p, "serial_number", None) or "?"
            print(f"     - {p.device}  (SN={sn})")
    else:
        print("  ⚠️ 没检测到 STM32 串口。分两种情况：")
        print("     a) 板子没插/没开机 → 检查 USB 线、按蓝键开机")
        if IS_WIN:
            print("     b) 没装 STM32 驱动 → 加 --fix 或 --install-driver 装")

    print("\n[4/4] STM32 驱动")
    if ok_drv:
        print(f"  ✅ {drv_note}")
    else:
        print(f"  ❌ {drv_note}")
        print(f"     官网：{STM32_DRIVER_URL}")

    print("\n" + "=" * 60)
    if _all_ready(results):
        print("  ✅ 环境全部就绪，可以直接跑：")
        _print_launch_hint()
        return 0

    print("  ⚠️ 环境没就绪，按上面 [1]-[4] 的提示修，或直接加 --fix 一键修复。")
    if not ok_drv and IS_WIN:
        if open_driver or (input("\n要现在打开 STM32 驱动下载页吗？(y/n): ").strip().lower() == "y"):
            print("  正在打开浏览器…")
            webbrowser.open(STM32_DRIVER_URL)
    return 1


def _print_summary(results):
    ok_py, py_ver = results["python"]
    ok_dep, missing = results["deps"]
    ok_board, ports = results["board"]
    ok_drv, drv_note = results["driver"]
    print(f"  Python:   {'✅' if ok_py else '❌'} {py_ver}")
    print(f"  依赖包:   {'✅' if ok_dep else '❌ ' + '、'.join(p for _, p in missing)}")
    print(f"  采集板:   {'✅' if ok_board else '⚠️ 没检测到'}"
          + (f"（{len(ports)} 块）" if ok_board else ""))
    print(f"  STM32 驱动: {'✅' if ok_drv else '❌'} {drv_note}")


def _all_ready(results):
    ok_py, _ = results["python"]
    ok_dep, _ = results["deps"]
    ok_drv, _ = results["driver"]
    return ok_py and ok_dep and ok_drv


def _print_launch_hint():
    if IS_WIN:
        print("     .venv\\Scripts\\python main.py config64.yaml")
        print("     或直接双击 start.bat")
    else:
        print("     .venv/bin/python main.py config64.yaml")
        print("     或 bash start.sh")


if __name__ == "__main__":
    raise SystemExit(main())
