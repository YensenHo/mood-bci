#!/usr/bin/env python3
"""detect_boards.py —— 4 块 RT-BCI 板插上后：自动识别串口 + 一键校准 64 通道都在流。

功能
----
1. 自动扫描 STM32 虚拟串口（VID 0x0483 / 描述含 STM32，兜底 usbmodem）。
2. 逐板握手（0xF0 + b，复用 OpenBCISource 的协议逻辑），读 2 秒数据。
3. 检查每板 16 通道是否都在流（非平线），汇总 64 通道状态。
4. 打印可直接填 config64.yaml 的 ports 列表。

通道状态口径
------------
  - 平线 (rms < 1 µV)     ：通道没流（死通道 / 板问题）
  - 打满 (rms > 100k µV)  ：在流，但电极悬空（接触不良，信号被噪声打满）
  - OK   (其余)           ：在流且有生理范围信号

  判定「在流」= 非平线。所以「64/64 通道在流」只证明 4 块板数据链路全通，
  「打满」的通道还需另外贴电极（那是接触问题，不是链路问题）。

运行（隔离环境）
--------------
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \\
        .venv/bin/python scripts/detect_boards.py [读取秒数]

注：4 块板 USB 枚举顺序不一定等于你物理上「板1/2/3/4」的顺序（板子同型号同 VID）。
    先用本脚本确认 4 路都在流，再逐板贴一个电极看哪个通道有反应，反推端口→板对应关系，
    最后把 ports 按物理板顺序填进 config64.yaml。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from eegscope.sources.openbci import OpenBCISource
from eegscope.sources.ports import detect_stm32_ports

FULL_SCALE_UV = 187500.0  # gain 24x 满量程 ±187.5 mV


def calibrate_board(port: str, seconds: float = 2.0):
    """对一块板握手 + 读数据。返回 (16, n) 数据；失败返回 None。"""
    src = OpenBCISource(port, n_channels=16)
    try:
        src.start()
        time.sleep(0.8)  # 等握手完成 + 开始流
        data = src.read(max(1, int(seconds * src.sample_rate)))
        src.stop()
        if data.shape[1] <= 1:
            return None  # 只拿到兜底 1 样本 = 实际没流
        return data
    except Exception:
        try:
            src.stop()
        except Exception:
            pass
        return None


def channel_states(data):
    """逐通道判定状态。返回 list[dict]（ch/rms/peak/state）。"""
    data = np.asarray(data, dtype=np.float64)
    states = []
    for i in range(data.shape[0]):
        x = data[i]
        rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
        peak = float(np.abs(x).max()) if x.size else 0.0
        if rms < 1.0:
            state = "平线"
        elif rms > 100_000.0:
            state = "打满"
        else:
            state = "OK"
        states.append({"ch": i + 1, "rms": rms, "peak": peak, "state": state})
    return states


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0

    ports = detect_stm32_ports()
    print(f"扫描到 {len(ports)} 个 STM32 串口:")
    for i, p in enumerate(ports, 1):
        sn = p.serial_number or "?"
        desc = (p.description or "").strip()
        print(f"  [{i}] {p.device}  SN={sn}  {desc}")

    if not ports:
        print("\n❌ 没检测到任何 STM32 串口。请检查：")
        print("   - 4 块板是否都开机（蓝键开机，绿灯常亮）")
        print("   - USB 线 + Type-C 转 USB-A 转接头是否插好")
        print("   - 集线器是否接到 Mac")
        return 1

    if len(ports) != 4:
        print(f"\n⚠️  预期 4 块板，实际 {len(ports)} 块。只校准检测到的这些。\n")

    print(f"\n逐板校准（每板握手 + 读 {seconds:g} 秒）…\n")
    n_board_ok = 0
    n_streaming = 0
    n_ok = 0
    port_list = []
    for i, p in enumerate(ports, 1):
        port_list.append(p.device)
        print(f"板{i} ({p.device}):", end=" ", flush=True)
        data = calibrate_board(p.device, seconds)
        if data is None:
            print("✗ 握手失败 / 无数据")
            continue
        n_ch, n = data.shape
        states = channel_states(data)
        stream = sum(1 for s in states if s["state"] != "平线")
        ok = sum(1 for s in states if s["state"] == "OK")
        n_board_ok += 1
        n_streaming += stream
        n_ok += ok
        print(f"{n_ch}ch × {n} 样本 | 在流 {stream}/16 | OK {ok}/16")
        for s in states:
            print(f"    ch{s['ch']:2d}: rms={s['rms']:9.1f}  peak={s['peak']:9.1f} µV  [{s['state']}]")

    print("\n" + "=" * 56)
    print(f"结果：{n_board_ok}/{len(ports)} 块板正常 | {n_streaming}/64 通道在流 | OK {n_ok}/64")
    if n_board_ok == len(ports) == 4 and n_streaming == 64:
        print("✅ 4 块板 64 通道数据链路全部打通")
    else:
        print("⚠️  有板/通道未就绪（见上面明细），检查对应板")

    print("\n串口列表（按检测顺序，填 config64.yaml 的 ports）:")
    for i, dev in enumerate(port_list, 1):
        print(f'  - "{dev}"   # 板{i}')

    return 0 if (n_board_ok == 4 and n_streaming == 64) else 1


if __name__ == "__main__":
    raise SystemExit(main())
