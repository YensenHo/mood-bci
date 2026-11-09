#!/usr/bin/env python3
"""eegscope 入口。

用法：
    python main.py                 # 使用同目录下的 config.yaml
    python main.py path/to.yaml    # 指定配置文件
"""
from __future__ import annotations

import os
import sys

from eegscope.app import run_app
from eegscope.config import load_config
from eegscope.sources import make_source

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "config.yaml")


def _friendly_source_error() -> int:
    """启动失败时打印大白话排查建议（不抛一堆英文 traceback）。"""
    print("\n" + "=" * 60)
    print("  ❌ 没连上采集板，启动失败。")
    print("=" * 60)
    print("  常见原因（按顺序查）：")
    print("   1. 板子没开机 / USB 没插好 → 按蓝键开机、重插 USB")
    print("   2. 没装 STM32 驱动（Windows 必装）→ 见下")
    print("   3. Mac 用 Type-C 直连不识别 → 必须经 USB-A 侧转接")
    print()
    print("  一键自检 + 查缺啥：")
    if sys.platform.startswith("win"):
        print("     .venv\\Scripts\\python scripts\\check_env.py")
    else:
        print("     .venv/bin/python scripts/check_env.py")
    print()
    print("  或先扫描串口看看：")
    if sys.platform.startswith("win"):
        print("     .venv\\Scripts\\python scripts\\detect_boards.py")
    else:
        print("     .venv/bin/python scripts/detect_boards.py")
    return 1


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG
    try:
        cfg = load_config(path)
    except FileNotFoundError:
        print(f"❌ 找不到配置文件：{path}")
        return 1

    # 数据源（真实板要串口；合成源无需硬件）
    try:
        source = make_source(cfg)
    except ValueError as e:
        print(f"[eegscope] {e}")
        return _friendly_source_error()
    except Exception as e:  # 其它意外（串口打开失败等）也走友好提示
        print(f"[eegscope] 启动数据源失败：{e}")
        return _friendly_source_error()

    # 采样率以数据源实际值为准（真实硬件可能是 250Hz 或 16ch Daisy 的 125Hz）
    if abs(source.sample_rate - cfg.sample_rate) > 1e-9:
        print(f"[eegscope] 采样率以数据源为准: {cfg.sample_rate:g} -> "
              f"{source.sample_rate:g} Hz")
        cfg.sample_rate = source.sample_rate

    print(f"[eegscope] 数据源就绪：{type(source).__name__} "
          f"（{source.n_channels} 通道）")
    run_app(cfg, source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
