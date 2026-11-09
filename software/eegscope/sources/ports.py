"""跨平台串口扫描：识别 STM32 虚拟串口。

Windows = COM3 / COM4 …；macOS = /dev/cu.usbmodemXXX；Linux = /dev/ttyACMx / ttyUSBx。
pyserial 的 list_ports.comports() 三平台都返回一致的 ListPortInfo，带 vid/pid/description，
所以优先用 VID 0x0483（STMicroelectronics STM32 USB CDC）精确匹配，三平台通用；
匹配不到时按平台关键字兜底。
"""
from __future__ import annotations

import sys

import serial.tools.list_ports

ST_VID = 0x0483  # STMicroelectronics STM32 USB CDC (VCP)


def detect_stm32_ports():
    """返回 STM32 虚拟串口的 ListPortInfo 对象列表（跨平台）。

    优先 VID 0x0483 / 描述含 STM32；兜底按平台关键字：
    Windows=COM、macOS=usbmodem、Linux=ttyACM/ttyUSB。
    """
    allp = list(serial.tools.list_ports.comports())
    stm = [p for p in allp
           if p.vid == ST_VID or "STM32" in (p.description or "").upper()]
    if stm:
        return stm
    if sys.platform.startswith("win"):
        return [p for p in allp if p.device.upper().startswith("COM")]
    if sys.platform.startswith("darwin"):
        return [p for p in allp if "usbmodem" in p.device]
    return [p for p in allp if "ttyACM" in p.device or "ttyUSB" in p.device]


def detect_stm32_device_names():
    """返回 STM32 串口设备名列表（字符串，用于自动填 config 的 ports）。"""
    return [p.device for p in detect_stm32_ports()]
