"""ISC 监控窗口（神经电影学主指标显示）。

场景：N 个观众各自跑「参与者端」软件（开「上传」），数据实时传到这台「监控端」。
本窗口收 N 人的脑电、各自滤波、算跨被试同步 ISC，实时显示：
  - 同步度（跨通道平均 ISC，-1~1）
  - 每个通道的 ISC（哪些电极最同步 = 这片子此刻最能"抓住"大家）
  - 在线人数 / 配对数

用法：python main.py --isc config_remote.yaml
"""
from __future__ import annotations

import sys

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QLabel, QMainWindow, QVBoxLayout,
                               QWidget)

from .dsp import SignalChain
from .isc import IscAnalyzer
from .remote import StreamServer
from .viz import setup_theme


class IscPanel(QWidget):
    """ISC 显示面板：同步度大字 + 每通道 ISC 柱状图 + 在线人数。"""

    def __init__(self, channel_names, fs: float):
        super().__init__()
        self.fs = float(fs)
        self.names = list(channel_names)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        title = QLabel("跨被试同步 ISC（神经电影学主指标）")
        title.setStyleSheet("font-weight: bold; font-size: 15px; color: #e0e0e0;")
        layout.addWidget(title)
        hint = QLabel("同步高 = 这一刻内容\"抓住\"了大家的大脑；同步低 = 掉点。")
        hint.setStyleSheet("color: #999; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.summary_label = QLabel("同步度 = --")
        f = self.summary_label.font()
        f.setPointSize(30)
        f.setBold(True)
        self.summary_label.setFont(f)
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.summary_label.setStyleSheet("color: #888;")
        layout.addWidget(self.summary_label)

        self.people_label = QLabel("等待参与者连入…（参与者端开「上传」）")
        self.people_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.people_label.setStyleSheet("color: #bbb; font-size: 13px;")
        layout.addWidget(self.people_label)

        # 每通道 ISC 柱状图
        chart_title = QLabel("各通道同步度（同一电极，N 人脑电的相关）")
        chart_title.setStyleSheet("color: #bbb; font-size: 12px;")
        layout.addWidget(chart_title)
        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(260)
        self.plot.setYRange(-0.2, 1.0)
        self.plot.setLabel("bottom", "电极")
        self.plot.setLabel("left", "ISC")
        self.plot.addItem(pg.InfiniteLine(0, angle=0, pen=pg.mkPen("#555")))
        self.bar = pg.BarGraphItem(
            x=np.arange(len(self.names)), height=[0.0] * len(self.names),
            width=0.6, brushes=[pg.mkBrush("#4caf50")] * len(self.names))
        self.plot.addItem(self.bar)
        self.plot.getAxis("bottom").setTicks(
            [[(i, n) for i, n in enumerate(self.names)]])
        layout.addWidget(self.plot, stretch=1)

    def update_metrics(self, summary, per_ch, participants, n_pairs):
        """刷新：summary 为跨通道平均 ISC（或 None），per_ch 为 (n_ch,) 或 None。"""
        if summary is None:
            self.summary_label.setText("同步度 = --")
            self.summary_label.setStyleSheet("color: #888;")
        else:
            color = "#4caf50" if summary >= 0.1 else ("#ffb74d" if summary >= 0 else "#f44336")
            self.summary_label.setText(f"同步度 = {summary:+.3f}")
            self.summary_label.setStyleSheet(f"color: {color};")

        n = len(participants)
        if n < 2:
            self.people_label.setText(
                f"在线 {n} 人（需 ≥2 人才能算同步）——参与者端开「上传」")
        else:
            self.people_label.setText(f"在线 {n} 人 · {n_pairs} 对两两相关")

        if per_ch is None or len(per_ch) == 0:
            self.bar.setOpts(height=[0.0] * len(self.names),
                             brushes=[pg.mkBrush("#555")] * len(self.names))
            return
        heights = [float(per_ch[i]) if i < len(per_ch) else 0.0
                   for i in range(len(self.names))]
        brushes = []
        for h in heights:
            brushes.append(pg.mkBrush("#4caf50" if h >= 0.1 else
                                     ("#ffb74d" if h >= 0 else "#f44336")))
        self.bar.setOpts(height=heights, brushes=brushes)


class IscMonitorWindow(QMainWindow):
    """监控端窗口：收多人数据 → 各自滤波 → ISC → 显示。"""

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        net = cfg.source.network
        self.fs = float(cfg.sample_rate)
        self.n_ch = len(cfg.channels)
        self.names = list(cfg.channels) or [f"ch{i + 1}" for i in range(self.n_ch)]

        self.server = StreamServer(host=net.host, port=net.port,
                                   n_channels=self.n_ch)
        self.isc = IscAnalyzer(self.fs, self.n_ch,
                               window_seconds=cfg.analysis.window_seconds,
                               channel_names=self.names)
        self._dsps: dict[str, SignalChain] = {}

        self.setWindowTitle("eegscope — 跨被试同步 ISC 监控（神经电影学）")
        self.panel = IscPanel(self.names, self.fs)
        self.setCentralWidget(self.panel)

        self.server.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(100)  # 100ms 轮询

    def _dsp_for(self, participant: str) -> SignalChain:
        dsp = self._dsps.get(participant)
        if dsp is None:
            dsp = SignalChain(self.fs, self.cfg.dsp.bandpass_hz,
                              self.cfg.dsp.notch_hz, self.cfg.dsp.notch_q)
            self._dsps[participant] = dsp
        return dsp

    def _tick(self):
        for p in self.server.participants:
            chunk = self.server.get(participant=p, timeout=0)
            if chunk is None:
                continue
            filtered = self._dsp_for(p).process(chunk)
            self.isc.add(p, filtered)
        per_ch, summary, n_pairs = self.isc.compute()
        self.panel.update_metrics(summary, per_ch, self.server.participants, n_pairs)

    def closeEvent(self, event):
        self.timer.stop()
        self.server.stop()
        super().closeEvent(event)


def run_isc_monitor(cfg):
    """创建 QApplication、启动 ISC 监控窗口并进入事件循环。"""
    app = QApplication.instance() or QApplication(sys.argv)
    setup_theme()
    win = IscMonitorWindow(cfg)
    win.resize(900, 700)
    win.show()
    sys.exit(app.exec())
