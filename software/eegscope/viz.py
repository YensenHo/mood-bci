"""pyqtgraph 可视化面板：堆叠波形 + 频谱 + 情绪指标（暗色主题）。"""
from __future__ import annotations

from collections import deque

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QLabel, QVBoxLayout, QWidget)

# 频带配色（r, g, b）
_BAND_COLORS = {
    "delta": (120, 120, 120),
    "theta": (150, 150, 150),
    "alpha": (76, 175, 80),
    "beta": (255, 152, 0),
    "gamma": (233, 30, 99),
}

_CHANNEL_PALETTE = [
    (90, 200, 250), (250, 170, 90), (160, 220, 120), (240, 130, 160),
    (140, 170, 255), (220, 210, 90), (120, 210, 210), (230, 150, 220),
]


def setup_theme() -> None:
    pg.setConfigOptions(background=(16, 16, 20), foreground=(220, 220, 220),
                        antialias=True)


class WaveformPanel(pg.PlotWidget):
    """多通道堆叠滚动波形（显示已滤波信号）。"""

    def __init__(self, n_channels, channel_names, window_seconds, sample_rate,
                 spacing_uv=80.0):
        super().__init__()
        self.n_ch = n_channels
        self.names = list(channel_names)
        self.fs = float(sample_rate)
        self.window_sec = float(window_seconds)
        self.spacing = float(spacing_uv)
        self.N = int(self.window_sec * self.fs)
        self.hist = np.zeros((n_channels, 0), dtype=np.float64)

        self.curves = []
        for i in range(n_channels):
            col = _CHANNEL_PALETTE[i % len(_CHANNEL_PALETTE)]
            c = self.plot(pen=pg.mkPen(col, width=1))
            self.curves.append(c)

        self.setLabel("bottom", "时间", units="s")
        self.showGrid(x=True, y=True, alpha=0.15)
        self.setXRange(-self.window_sec, 0.0, padding=0)
        self._set_y_ticks()

    def _set_y_ticks(self):
        ticks = [(-i * self.spacing, self.names[i]) for i in range(self.n_ch)]
        self.getAxis("left").setTicks([ticks])
        lo = -(self.n_ch - 1) * self.spacing - self.spacing
        hi = self.spacing
        self.setYRange(lo, hi, padding=0)

    def push(self, chunk: np.ndarray) -> None:
        """追加一块已滤波数据 (n_ch, n)，滚动刷新。"""
        chunk = np.asarray(chunk, dtype=np.float64)
        self.hist = np.concatenate([self.hist, chunk], axis=1)
        if self.hist.shape[1] > self.N:
            self.hist = self.hist[:, -self.N:]
        n = self.hist.shape[1]
        t = (np.arange(n) - (n - 1)) / self.fs  # 最新样本在 x=0
        for i in range(self.n_ch):
            self.curves[i].setData(t, self.hist[i] - i * self.spacing)


class SpectrumPanel(pg.PlotWidget):
    """PSD 频谱面板（log-y），叠加频带着色区域。"""

    def __init__(self, sample_rate, bands):
        super().__init__()
        self.fs = float(sample_rate)
        self.bands = bands
        self.curve = self.plot(pen=pg.mkPen((90, 200, 255), width=1))
        self.setLabel("bottom", "频率", units="Hz")
        self.setLabel("left", "PSD", units="µV²/Hz")
        self.setLogMode(x=False, y=True)
        self.setXRange(0.0, 45.0, padding=0)
        self.showGrid(x=True, y=True, alpha=0.15)

        for name, (lo, hi) in bands.items():
            r, g, b = _BAND_COLORS.get(name, (120, 120, 120))
            region = pg.LinearRegionItem(
                [lo, hi], movable=False, brush=pg.mkBrush(r, g, b, 40), pen=None)
            self.addItem(region)

    def update_psd(self, freqs, psd) -> None:
        freqs = np.asarray(freqs, dtype=np.float64).ravel()
        psd = np.asarray(psd, dtype=np.float64).ravel()
        self.curve.setData(freqs, psd)


class EmotionPanel(QWidget):
    """情绪指标面板：FAA 值 + 分频带功率条 + 效价/唤醒度二维指示。"""

    def __init__(self, bands):
        super().__init__()
        self.bands = bands
        self.band_names = list(bands.keys())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        title = QLabel("情绪指标（单被试辅助指标）")
        title.setStyleSheet("font-weight: bold; font-size: 14px; color: #e0e0e0;")
        layout.addWidget(title)

        self.faa_label = QLabel("FAA = --")
        f = QFont("Menlo")
        f.setPointSize(22)
        f.setBold(True)
        self.faa_label.setFont(f)
        self.faa_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.faa_label.setStyleSheet("color: #888;")
        layout.addWidget(self.faa_label)

        # 分频带功率条
        bar_layout = QVBoxLayout()
        bar_title = QLabel("分频带功率")
        bar_title.setStyleSheet("color: #bbb; font-size: 12px;")
        bar_layout.addWidget(bar_title)
        self.bar_plot = pg.PlotWidget()
        self.bar_plot.setFixedHeight(160)
        self.bar_plot.showGrid(x=False, y=True, alpha=0.15)
        self.bar_plot.setLabel("left", "dB")
        self.bar_item = pg.BarGraphItem(x=np.arange(len(self.band_names)),
                                        height=[0] * len(self.band_names),
                                        width=0.6,
                                        brushes=[pg.mkBrush(*_BAND_COLORS.get(b, (120, 120, 120)))
                                                 for b in self.band_names])
        self.bar_plot.addItem(self.bar_item)
        self.bar_plot.getAxis("bottom").setTicks(
            [[(i, b) for i, b in enumerate(self.band_names)]])
        self.bar_plot.setYRange(-60, 40, padding=0)
        bar_layout.addWidget(self.bar_plot)
        layout.addLayout(bar_layout)

        # 效价/唤醒度二维指示
        va_title = QLabel("效价(→) × 唤醒度(↑)")
        va_title.setStyleSheet("color: #bbb; font-size: 12px;")
        layout.addWidget(va_title)
        self.va_plot = pg.PlotWidget()
        self.va_plot.setFixedHeight(180)
        self.va_plot.setXRange(-1.1, 1.1, padding=0)
        self.va_plot.setYRange(-1.1, 1.1, padding=0)
        self.va_plot.setLabel("bottom", "效价")
        self.va_plot.setLabel("left", "唤醒度")
        self.va_plot.addItem(pg.InfiniteLine(0, angle=0, pen=pg.mkPen("#555")))
        self.va_plot.addItem(pg.InfiniteLine(0, angle=90, pen=pg.mkPen("#555")))
        self.va_trail = self.va_plot.plot([], [], pen=None, symbol="o",
                                          symbolSize=3, symbolBrush="#556")
        self.va_point = self.va_plot.plot([], [], pen=None, symbol="o",
                                          symbolSize=12, symbolBrush="#4caf50")
        self.va_plot.addItem(pg.TextItem("正性", color="#999", anchor=(0.5, 1)))
        self.va_trail_data = deque(maxlen=60)
        layout.addWidget(self.va_plot)

    def update_metrics(self, metrics: dict) -> None:
        """用最新的情绪指标 dict 刷新本面板（不覆盖 QWidget.update()）。"""
        # FAA
        faa = metrics.get("faa")
        if metrics.get("faa_ok") and faa is not None:
            color = "#4caf50" if faa >= 0 else "#f44336"
            self.faa_label.setText(f"FAA = {faa:+.3f}")
        else:
            color = "#888"
            self.faa_label.setText("需 F3/F4")
        self.faa_label.setStyleSheet(f"color: {color};")

        # 频带功率条（dB）
        bands = metrics.get("bands") or {}
        heights = []
        for b in self.band_names:
            p = bands.get(b)
            if p is None:
                heights.append(-60.0)
            else:
                heights.append(float(10.0 * np.log10(max(p, 1e-12))))
        self.bar_item.setOpts(height=heights)

        # 效价/唤醒度二维点
        v = metrics.get("valence")
        a = metrics.get("arousal")
        if v is not None and a is not None:
            self.va_trail_data.append((float(v), float(a)))
            self.va_point.setData([float(v)], [float(a)])
            if self.va_trail_data:
                xs = [p[0] for p in self.va_trail_data]
                ys = [p[1] for p in self.va_trail_data]
                self.va_trail.setData(xs, ys)
