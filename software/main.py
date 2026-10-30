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


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG
    cfg = load_config(path)
    source = make_source(cfg)
    # 采样率以数据源实际值为准（真实硬件可能是 250Hz 或 16ch Daisy 的 125Hz）
    if abs(source.sample_rate - cfg.sample_rate) > 1e-9:
        print(f"[eegscope] 采样率以数据源为准: {cfg.sample_rate:g} -> "
              f"{source.sample_rate:g} Hz")
        cfg.sample_rate = source.sample_rate
    run_app(cfg, source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
