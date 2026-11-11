"""数据源工厂。真实 ADS1299 串口驱动在此注册接入。"""
from __future__ import annotations

from .base import DataSource
from .synthetic import SyntheticSource
from ..config import DEFAULT_CHANNELS, DEFAULT_CHANNELS_64

__all__ = ["DataSource", "SyntheticSource", "make_source"]


def make_source(cfg):
    """根据 config 的 `source.type` 构造数据源实例。

    这是后续接入真实硬件的唯一入口：实现一个继承 `DataSource` 的
    `ADS1299Source`（start/stop/read + sample_rate/n_channels），然后在这里
    加一个分支即可，其余管线（DSP/分析/可视化）完全不用改。
    """
    stype = (cfg.source.type or "synthetic").lower()

    if stype == "synthetic":
        s = cfg.source.synthetic
        return SyntheticSource(
            sample_rate=cfg.sample_rate,
            channel_names=cfg.channels,
            seed=s.seed,
            alpha_level=s.alpha_level,
            blink_rate=s.blink_rate,
            alpha_uv=s.alpha_uv,
            noise_uv=s.noise_uv,
            mains_uv=s.mains_uv,
            harmonic_uv=s.harmonic_uv,
            drift_uv=s.drift_uv,
        )

    if stype == "openbci":
        from .openbci import OpenBCISource
        from .multiboard import MultiBoardSource
        from .ports import detect_stm32_device_names
        o = cfg.source.openbci
        # 端口列表：ports 优先，否则回落到单 port；空或含 "AUTO" → 跨平台自动扫描
        ports = [p for p in (o.ports or []) if p]
        if not ports and o.port:
            ports = [o.port]
        if not ports or any(str(p).strip().upper() == "AUTO" for p in ports):
            auto = detect_stm32_device_names()
            if auto:
                ports = auto
            else:
                # 配了 "AUTO" 但没扫到任何 STM32 串口：清掉字面 "AUTO"，
                # 让下面走「没找到串口」的友好报错，而不是拿 "AUTO" 去开串口崩溃。
                ports = [p for p in ports if str(p).strip().upper() != "AUTO"]
        if not ports:
            raise ValueError(
                "openbci 数据源没找到串口：config 没配 port/ports，"
                "自动扫描也没发现 STM32 串口。请确认板子开机、USB 插好，"
                "或先跑 scripts/detect_boards.py 列出串口")
        n_boards = len(ports)
        # 每块板通道数：显式给 n_channels；否则按「channels 长度 / 板数」自动推断
        n_ch_per_board = o.n_channels if o.n_channels > 0 else max(1, len(cfg.channels) // n_boards)
        total_ch = n_ch_per_board * n_boards
        names = cfg.channels if len(cfg.channels) == total_ch else None
        if names is None:
            # 通道名数量对不上（如 16 通道 config 接了 2/3/4 块板）。用标准 10-10
            # 布局兜底，保证质量面板显示电极名、脑地形图能定位；真实物理接线顺序
            # 仍以 config64.yaml 为准。16 通道用项目标准 16 导；其它按 10-10 布局
            # 取前缀，不够用 NC 补齐。
            if total_ch == 16:
                names = list(DEFAULT_CHANNELS)
            else:
                full = list(DEFAULT_CHANNELS_64)
                names = (full + ["NC"] * total_ch)[:total_ch]
        gain_code = None if o.gain_code < 0 else o.gain_code
        common = dict(baudrate=o.baudrate, gain=o.gain,
                      scale_uv=o.scale_uv if o.scale_uv > 0 else None)
        if n_boards == 1:
            return OpenBCISource(
                port=ports[0], n_channels=n_ch_per_board,
                channel_names=names, gain_code=gain_code, **common,
            )
        return MultiBoardSource(
            ports=ports, n_channels_per_board=n_ch_per_board,
            channel_names=names, gain_code=gain_code, **common,
        )

    if stype == "network":
        from .network import NetworkSource
        n = cfg.source.network
        return NetworkSource(
            host=n.host, port=n.port,
            channel_names=cfg.channels, sample_rate=cfg.sample_rate,
        )

    raise ValueError(f"未知数据源类型: {cfg.source.type!r}（当前支持: synthetic, openbci, network）")
