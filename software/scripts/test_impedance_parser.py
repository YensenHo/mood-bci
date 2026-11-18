#!/usr/bin/env python3
"""Headless 测试：parse_impedance_response（无需真实硬件）。

覆盖 'z' 阻抗响应常见格式：ChN/Channel N/ZN/CHN + Ohm/kOhm/k 单位、
纯数字列表回退、空/不可解析返回 None、通道号越界忽略。

运行（隔离环境）：
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \\
        .venv/bin/python scripts/test_impedance_parser.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eegscope.sources.openbci import parse_impedance_response


def main() -> int:
    # [1] ChN + Ohm
    r = parse_impedance_response("Ch1: 12345 Ohm\nCh2: 8000 Ohm")
    assert r == {1: 12345.0, 2: 8000.0}, r
    print(f"[1] ChN+Ohm OK: {r}")

    # [2] Channel N + kOhm（单位 ×1000）
    r = parse_impedance_response("Channel 2: 12.3 kOhm")
    assert r == {2: 12300.0}, r
    print(f"[2] Channel+kOhm OK: {r}")

    # [3] ZN 前缀，无单位（默认 Ω）
    r = parse_impedance_response("Z3: 5200")
    assert r == {3: 5200.0}, r
    print(f"[3] ZN 无单位 OK: {r}")

    # [4] CHN + 'k' 简写单位
    r = parse_impedance_response("CH4: 5.2 k")
    assert r == {4: 5200.0}, r
    print(f"[4] CHN+k OK: {r}")

    # [5] 纯数字列表（无字母）→ 按 1..N
    r = parse_impedance_response("5200, 6100, 4800, 7200, 5300, 6000, 5500, 6800", n_channels=8)
    assert r == {1: 5200.0, 2: 6100.0, 3: 4800.0, 4: 7200.0,
                 5: 5300.0, 6: 6000.0, 7: 5500.0, 8: 6800.0}, r
    print(f"[5] 纯数字列表 OK: {list(r.keys())} 通道")

    # [6] 空 / None → None
    assert parse_impedance_response("") is None
    assert parse_impedance_response(None) is None
    print("[6] 空/None → None OK")

    # [7] 有字母但无通道标记/数字 → None（不臆造）
    assert parse_impedance_response("Error: command not recognized") is None
    print("[7] 不可解析 → None OK")

    # [8] 通道号越界（>64）忽略，合法通道保留
    r = parse_impedance_response("Ch1: 5000 Ohm\nCh99: 999 Ohm")
    assert r == {1: 5000.0}, r
    print(f"[8] 通道号越界忽略 OK: {r}")

    print("\n✅ parse_impedance_response 测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
