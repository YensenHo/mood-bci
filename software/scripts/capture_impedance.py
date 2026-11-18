#!/usr/bin/env python3
"""发「z」命令抓 OpenBCI 兼容板的导联阻抗响应（验证 M3：ADS1299 LOFF 阻抗检测）。

板子到手、跑通 capture_serial.py 之后跑这个，判断克隆固件是否支持 'z' 阻抗检测、
响应格式是什么（本机无板载固件源码，格式未知，所以先抓原始字节 + 文本，再 best-effort 解析）。

用法（隔离环境跑）：
    # 1) 先不带端口，列出可用串口
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \\
        .venv/bin/python scripts/capture_impedance.py

    # 2) 指定串口发 z、抓 3 秒响应
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \\
        .venv/bin/python scripts/capture_impedance.py /dev/cu.usbserial-XXXX -n 3

诚实边界：本脚本只「发 z → 抓响应 → 打印 + best-effort 解析」，不下结论。判断
「是否支持、阻抗值是否合理（干>100kΩ 报警）」需人看原始输出，别拿解析结果当实测。
"""
from __future__ import annotations

import argparse
import sys
import time


def list_ports():
    try:
        import serial.tools.list_ports
        return [(p.device, p.description) for p in serial.tools.list_ports.comports()]
    except Exception:
        return []


def read_until(ser, marker: bytes, timeout: float = 2.0) -> bytes:
    """读到 marker 出现为止（串口读，返回累积字节）。超时也返回已读到的。"""
    buf = b""
    deadline = time.time() + timeout
    while marker not in buf and time.time() < deadline:
        d = ser.read(ser.in_waiting or 1)
        if d:
            buf += d
    return buf


def main() -> int:
    ap = argparse.ArgumentParser(description="抓 OpenBCI 板 'z' 阻抗响应")
    ap.add_argument("port", nargs="?", help="串口设备，如 /dev/cu.usbserial-XXXX")
    ap.add_argument("-b", "--baudrate", type=int, default=115200)
    ap.add_argument("-n", "--seconds", type=float, default=3.0)
    args = ap.parse_args()

    ports = list_ports()
    print("可用串口:")
    if not ports:
        print("  （无）——请确认采集板已插 USB")
    for dev, desc in ports:
        print(f"  {dev}    {desc}")

    if not args.port:
        print("\n请指定 --port（从上面列表选），例如：")
        print("  scripts/capture_impedance.py /dev/cu.usbserial-XXXX -n 3")
        return 1

    import serial
    ser = serial.Serial(args.port, args.baudrate, timeout=0.1)

    # 1) 握手：0xF0 → 等 "$$$"（跟 OpenBCISource._handshake 一致）
    ser.reset_input_buffer()
    ser.write(b"\xF0")
    hand = read_until(ser, b"$$$", timeout=2.0)
    print(f"\n握手 0xF0 → 读到 {len(hand)} 字节，含 '$$$' = {'$$$'.encode() in hand}")

    # 2) 发 'z' 阻抗命令
    ser.reset_input_buffer()
    ser.write(b"z")
    print(f"已发 'z'，抓取 {args.seconds}s 响应 ...")

    # 3) 抓原始字节
    t0 = time.time()
    buf = bytearray()
    while time.time() - t0 < args.seconds:
        buf.extend(ser.read(ser.in_waiting or 1))
    ser.close()
    print(f"抓到 {len(buf)} 字节\n")

    # 4) 打印原始 hexdump（前 128 字节）
    n = min(128, len(buf))
    print("=== 原始字节（前 128）===")
    for i in range(0, n, 16):
        chunk = bytes(buf[i:i + 16])
        hexs = " ".join(f"{b:02x}" for b in chunk)
        ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"{i:06x}  {hexs:<48}  {ascii_}")

    # 5) 打印解码文本（跳过不可打印，保留换行）
    text = "".join(chr(b) if (32 <= b < 127 or b in (10, 13)) else "·" for b in buf)
    print("\n=== 解码文本 ===")
    print(text)

    # 6) best-effort 解析
    print("\n=== best-effort 阻抗解析 ===")
    try:
        from eegscope.sources.openbci import parse_impedance_response
        imp = parse_impedance_response("".join(chr(b) for b in buf if 32 <= b < 127), n_channels=8)
        if imp:
            for ch in sorted(imp):
                print(f"  通道 {ch}: {imp[ch]:.0f} Ω  ({imp[ch]/1000:.1f} kΩ)")
            print("  ⚠️ 以上为按常见格式的猜测解析，真机格式以原始字节为准；>100kΩ 提示重贴电极（M3）。")
        else:
            print("  未解析到阻抗值（克隆固件可能不支持 'z'，或响应格式不在预期内）——请看上面的原始字节。")
    except Exception as e:
        print(f"  解析失败：{e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
