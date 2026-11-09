#!/usr/bin/env python3
"""eegscope 统一入口（dev 与 PyInstaller 打包版共用）。

    eegscope.exe / python entry.py
        → 打开启动器（环境自检 + 一键修复 + 启动采集）

    eegscope.exe --app config64.yaml
    eegscope.exe config64.yaml
        → 直接跑采集软件（打包版里启动器按钮就是这么拉起自己的）
"""
from __future__ import annotations

import sys

from eegscope import _runtime


def _run_app(config_path: str) -> int:
    from main import run
    return run(config_path)


def main() -> int:
    args = [a for a in sys.argv[1:]]

    # 打包版首次运行：把 config 拷到 exe 目录，方便用户改 ports（dev 模式无需）
    _runtime.ensure_user_configs()

    config_path = None
    if args and args[0] == "--app":
        config_path = args[1] if len(args) > 1 else None
    elif args and args[0].endswith((".yaml", ".yml")):
        config_path = args[0]

    if config_path is not None:
        return _run_app(config_path)

    from launcher import main as launcher_main
    launcher_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
