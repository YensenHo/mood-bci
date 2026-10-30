#!/usr/bin/env python3
"""抓取串口原始字节，验证 OpenBCI 兼容板的真实协议（波特率/帧头/通道数/缩放）。

板子到手第一步就跑这个：抓几秒字节，看 0xA0 帧头是否连续、33 字节边界是否对齐、
样本计数是否按 Daisy 规则（bit0 区分主副板）变化。

用法（隔离环境跑）：
    # 1) 先不带 -o，列出可用串口
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
        .venv/bin/python scripts/capture_serial.py

    # 2) 指定串口抓 3 秒，打印前 128 字节 hexdump
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
        .venv/bin/python scripts/capture_serial.py /dev/cu.usbserial-XXXX -b 115200 -n 3

    # 3) 存文件，之后用 hexdump 细看
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
        .venv/bin/python scripts/capture_serial.py /dev/cu.usbserial-XXXX -n 5 -o raw.bin
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


def main() -> int:
    ap = argparse.ArgumentParser(description="抓 OpenBCI 串口原始字节验证协议")
    ap.add_argument("port", nargs="?", help="串口设备，如 /dev/cu.usbserial-XXXX")
    ap.add_argument("-b", "--baudrate", type=int, default=115200)
    ap.add_argument("-n", "--seconds", type=float, default=3.0)
    ap.add_argument("-o", "--out", help="输出文件（默认打印前 128 字节 hexdump）")
    args = ap.parse_args()

    ports = list_ports()
    print("可用串口:")
    if not ports:
        print("  （无）——请确认采集板已插 USB")
    for dev, desc in ports:
        print(f"  {dev}    {desc}")

    if not args.port:
        print("\n请指定 --port（从上面列表选），例如：")
        print("  scripts/capture_serial.py /dev/cu.usbserial-XXXX -n 3")
        return 1

    import serial
    ser = serial.Serial(args.port, args.baudrate, timeout=0.1)
    print(f"\n已打开 {args.port} @ {args.baudrate}，抓取 {args.seconds}s ...")
    t0 = time.time()
    buf = bytearray()
    while time.time() - t0 < args.seconds:
        buf.extend(ser.read(ser.in_waiting or 1))
    ser.close()
    print(f"抓到 {len(buf)} 字节 ({len(buf) / args.seconds:.0f} B/s)")

    if args.out:
        with open(args.out, "wb") as f:
            f.write(buf)
        print(f"已写 {args.out}")
    else:
        n = min(128, len(buf))
        for i in range(0, n, 16):
            chunk = bytes(buf[i:i + 16])
            hexs = " ".join(f"{b:02x}" for b in chunk)
            ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            print(f"{i:06x}  {hexs:<48}  {ascii_}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
