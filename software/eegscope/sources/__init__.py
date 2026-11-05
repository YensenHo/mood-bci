"""数据源工厂。真实 ADS1299 串口驱动在此注册接入。"""
from __future__ import annotations

from .base import DataSource
from .synthetic import SyntheticSource

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
        o = cfg.source.openbci
        # 端口列表：ports 优先，否则回落到单 port
        ports = [p for p in (o.ports or []) if p]
        if not ports and o.port:
            ports = [o.port]
        if not ports:
            raise ValueError(
                "openbci 数据源需要配置 port 或 ports（串口设备名），"
                "先跑 scripts/capture_serial.py 列出串口")
        n_boards = len(ports)
        # 每块板通道数：显式给 n_channels；否则按「channels 长度 / 板数」自动推断
        n_ch_per_board = o.n_channels if o.n_channels > 0 else max(1, len(cfg.channels) // n_boards)
        total_ch = n_ch_per_board * n_boards
        names = cfg.channels if len(cfg.channels) == total_ch else None
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

    raise ValueError(f"未知数据源类型: {cfg.source.type!r}（当前支持: synthetic, openbci）")
