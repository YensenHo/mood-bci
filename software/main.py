#!/usr/bin/env python3
"""eegscope 入口。

用法：
    python main.py                 # 使用同目录下的 config.yaml
    python main.py path/to.yaml    # 指定配置文件
"""
from __future__ import annotations

import os
import sys

from eegscope import _runtime
from eegscope.app import run_app
from eegscope.config import load_config
from eegscope.sources import make_source

DEFAULT_CONFIG = _runtime.resource_path("config.yaml")


def _show_frozen_error(message: str) -> None:
    """打包版（无控制台）里，把启动失败弹成 GUI 对话框。

    窗口化的 exe 看不到 print 输出，直接退出会变成「点了没反应 / 一闪而过」，
    所以失败时弹个看得见的错误框。dev 模式（有终端）不弹，照旧 print。
    """
    if not _runtime.IS_FROZEN:
        return
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "eegscope 启动失败", message)
    except Exception:  # noqa: BLE001
        pass


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
    _show_frozen_error(
        "没连上采集板。请检查：\n"
        "  1. 板子有没有开机（按蓝键，绿灯常亮）\n"
        "  2. USB 有没有插好\n"
        "  3. Windows 是否装好 STM32 驱动（回到启动器点「🛠 一键修复缺的东西」）\n"
        "  4. Mac 请用 USB-A 转接头（Type-C 直连可能不识别）")
    return 1


def run(config_path: str | None = None) -> int:
    """跑采集软件。config_path 为 None 时用默认 config.yaml。"""
    path = config_path or DEFAULT_CONFIG
    try:
        cfg = load_config(path)
    except FileNotFoundError:
        print(f"❌ 找不到配置文件：{path}")
        _show_frozen_error(f"找不到配置文件：{path}")
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
    try:
        run_app(cfg, source)
    except Exception as e:  # 串口打不开（端口变了/换了电脑）等，友好提示而非闪退
        print(f"[eegscope] 启动采集失败：{e}")
        _show_frozen_error(
            f"启动采集失败：{e}\n\n"
            "常见原因：\n"
            "1. 换电脑 / 串口变了 → 重新点「🔧 调试向导」识板\n"
            "2. 板子没开机（按蓝键，绿灯常亮）/ USB 没插好\n"
            "3. 多块板时，config 里的串口号是之前电脑的")
        return 1
    return 0


def run_isc(config_path: str | None = None) -> int:
    """跑 ISC 监控（神经电影学：多人跨被试同步）。config 需 source.type=network。"""
    path = config_path or DEFAULT_CONFIG
    try:
        cfg = load_config(path)
    except FileNotFoundError:
        print(f"❌ 找不到配置文件：{path}")
        _show_frozen_error(f"找不到配置文件：{path}")
        return 1
    if getattr(cfg.source, "type", "") != "network":
        print("[eegscope] ISC 监控需要 network 数据源（用 config_remote.yaml）")
        return 1
    from eegscope.isc_monitor import run_isc_monitor
    run_isc_monitor(cfg)
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--isc":
        return run_isc(args[1] if len(args) > 1 else None)
    path = args[0] if args else DEFAULT_CONFIG
    return run(path)


if __name__ == "__main__":
    raise SystemExit(main())
