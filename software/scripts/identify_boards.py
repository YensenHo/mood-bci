#!/usr/bin/env python3
"""identify_boards.py —— 摸电极识板：确定 4 个串口分别对应哪块物理板。

原理：电极悬空时通道「打满」（~18.7 万 µV 满量程）；用手捏住一个电极，
身体提供了对地回路，该通道 RMS 会从打满掉下来。脚本监测 4 块板 64 通道，
捕捉「打满 → 明显下降」，报出是哪个串口的哪个通道响应，再对照 config64.yaml
的接线顺序，直接告诉你「这个串口 = 板几」。

用法（隔离环境）：
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \\
        .venv/bin/python scripts/identify_boards.py

流程：脚本会依次让你捏 4 个电极（板1=F3、板2=T7、板3=T8、板4=O1），
每捏一个它自动识别对应的串口，最后打印「串口 → 板」映射和可直接粘贴的 ports 列表。
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from eegscope.config import load_config
from eegscope.sources.openbci import OpenBCISource
from eegscope.sources.ports import detect_stm32_ports

# 每块板挑一个容易找到的代表电极（ISC 核心，额/颞/颞/枕各一个）
REP_ELECTRODES = ["F3", "T7", "T8", "O1"]


def channel_rms(data):
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 1:
        data = data[None, :]
    return np.sqrt(np.mean(data * data, axis=1)) if data.shape[1] else np.zeros(data.shape[0])


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        cfg = load_config(os.path.join(here, "config64.yaml"))
        names = list(cfg.channels)
    except Exception:
        names = [f"ch{i + 1}" for i in range(64)]

    # 从 config 找 4 个代表电极的「板内本地通道」和「板号」
    reps = []
    for want in REP_ELECTRODES:
        try:
            gidx = names.index(want)
        except ValueError:
            print(f"⚠️ config64.yaml 里找不到电极 {want}，跳过")
            continue
        reps.append((want, (gidx % 16) + 1, (gidx // 16) + 1))  # (电极名, 本地通道, 板号)

    ports = detect_stm32_ports()
    print(f"检测到 {len(ports)} 个 STM32 串口:")
    for i, p in enumerate(ports, 1):
        print(f"  串口{i}: {p.device}  (SN={p.serial_number or '?'})")
    if len(ports) != 4:
        print(f"\n⚠️ 预期 4 块板，实际 {len(ports)} 块。请确认 4 块板都开机、USB 都插好。")
        if len(ports) < 4:
            return 1
    print()

    # 打开全部板，读基线
    srcs = [OpenBCISource(p.device, n_channels=16) for p in ports]
    for s in srcs:
        s.start()
    time.sleep(1.5)
    base = []
    for s in srcs:
        d = s.read(250)
        base.append(channel_rms(d) if d.shape[1] else np.zeros(16))
    print("基线已记录（未触摸，应基本打满）。\n")
    print("各板基线 RMS（µV，正常浮空≈18.7万）：")
    for bi in range(len(ports)):
        print(f"  串口{bi+1}: {[round(float(v)) for v in base[bi]]}")
    print()

    mapping = {}  # 板号 -> 串口下标
    for name, local_ch, board in reps:
        print(f"▶ 请捏住【板{board}】上的 {name} 电极，保持 2 秒后松开 …")
        found = None
        t0 = time.time()
        while time.time() - t0 < 20:
            for bi, s in enumerate(srcs):
                if base[bi][local_ch - 1] <= 60000:
                    continue  # 该通道本来不是浮空态，跳过
                hit = 0
                for _ in range(4):  # 连续 4 次(~0.6s)都低于 3 万µV 才算真触摸
                    d = s.read(15)
                    if d.shape[1] < 4:
                        break
                    if channel_rms(d)[local_ch - 1] < 30000:
                        hit += 1
                if hit == 4:
                    found = bi
                    break
            if found is not None:
                break
            time.sleep(0.1)
        if found is None:
            print(f"  ✗ 没检测到 {name} 的响应（捏久一点、捏实一点，别碰其它电极）\n")
            continue
        print(f"  ✅ 串口{found + 1} ({ports[found].device}) 本地通道{local_ch} 响应 → 板{board}\n")
        mapping[board] = found
        time.sleep(0.5)

    for s in srcs:
        try:
            s.stop()
        except Exception:
            pass

    print("=" * 56)
    print("识板结果：")
    ordered = []
    for b in sorted(mapping):
        bi = mapping[b]
        ordered.append(ports[bi].device)
        print(f"  板{b} = 串口{bi + 1}  {ports[bi].device}")
    missing = [b for b in range(1, len(reps) + 1) if b not in mapping]
    if missing:
        print(f"  ⚠️ 没识别到的板: {missing}（重新跑一遍，捏久一点）")
    print()

    if len(mapping) == 4:
        print("把这 4 个串口名按板1~板4 顺序填进 config64.yaml 的 ports：")
        print("  ports:")
        for i, dev in enumerate(ordered, 1):
            print(f'    - "{dev}"   # 板{i}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
