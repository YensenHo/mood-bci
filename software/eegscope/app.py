"""Qt 主窗口 + 数据流编排：source → dsp → analysis → viz。"""
from __future__ import annotations

import sys

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel,
                               QMainWindow, QPushButton, QSlider, QSplitter,
                               QVBoxLayout, QWidget)

from .analysis import EmotionAnalyzer, welch_psd
from .dsp import SignalChain
from .viz import EmotionPanel, SpectrumPanel, WaveformPanel, setup_theme


class MainWindow(QMainWindow):
    def __init__(self, cfg, source):
        super().__init__()
        self.cfg = cfg
        self.source = source

        self.dsp = SignalChain(cfg.sample_rate, cfg.dsp.bandpass_hz,
                               cfg.dsp.notch_hz, cfg.dsp.notch_q)
        self.analyzer = EmotionAnalyzer(cfg.sample_rate, cfg.channels,
                                        cfg.analysis.bands, cfg.analysis.faa_channels)

        self.setWindowTitle("eegscope — 实时脑电可视化 + 情绪分析（合成数据）")
        self._build_ui()

        # 分析/PSD 刷新周期（每 N 次 tick 算一次，约 500ms）
        self._analysis_every = max(1, int(0.5 * 1000 / cfg.viz.refresh_ms))
        self._tick_count = 0
        # 指标显示平滑（EMA），降低低 alpha 时 FAA/效价/唤醒度的噪声抖动
        self._ema_state = {}

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(cfg.viz.refresh_ms)
        self._frac = 0.0  # 分块读取的小数样本累积（保证平均采样率准确）

    # ---- UI ----
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)

        root.addLayout(self._build_controls())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.waveform = WaveformPanel(
            self.source.n_channels, self.source.channel_names,
            self.cfg.viz.window_seconds, self.cfg.sample_rate,
            self.cfg.viz.channel_spacing_uv)
        splitter.addWidget(self.waveform)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.spectrum = SpectrumPanel(self.cfg.sample_rate, self.cfg.analysis.bands)
        self.emotion = EmotionPanel(self.cfg.analysis.bands)
        right_layout.addWidget(self.spectrum, stretch=3)
        right_layout.addWidget(self.emotion, stretch=4)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([800, 380])
        root.addWidget(splitter, stretch=1)

    def _build_controls(self):
        bar = QHBoxLayout()
        bar.setSpacing(10)

        self.start_btn = QPushButton("暂停")
        self.start_btn.setCheckable(True)
        self.start_btn.toggled.connect(self._on_start_toggled)
        bar.addWidget(self.start_btn)

        bar.addWidget(QLabel("alpha 幅度（睁眼↔闭眼）"))
        self.alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.alpha_slider.setRange(0, 100)
        self.alpha_slider.setValue(int(self.source.alpha_level * 100))
        self.alpha_slider.valueChanged.connect(self._on_alpha_slider)
        self.alpha_slider.setFixedWidth(160)
        bar.addWidget(self.alpha_slider)

        self.eyes_btn = QPushButton("模拟闭眼")
        self.eyes_btn.setCheckable(True)
        self.eyes_btn.toggled.connect(self._on_eyes_toggled)
        bar.addWidget(self.eyes_btn)

        bar.addWidget(QLabel("PSD 通道"))
        self.ch_selector = QComboBox()
        self.ch_selector.addItems(self.source.channel_names)
        self.ch_selector.setCurrentIndex(0)
        bar.addWidget(self.ch_selector)

        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #999;")
        bar.addWidget(self.info_label)
        bar.addStretch(1)
        return bar

    # ---- 控制回调 ----
    def _on_start_toggled(self, checked):
        if checked:
            self.timer.stop()
            self.start_btn.setText("继续")
        else:
            self.timer.start(self.cfg.viz.refresh_ms)
            self.start_btn.setText("暂停")

    def _on_alpha_slider(self, value):
        self.source.set_alpha_level(value / 100.0)
        # 手动拖动旋钮时，同步取消“模拟闭眼”按钮状态
        if self.eyes_btn.isChecked() and value < 90:
            self.eyes_btn.blockSignals(True)
            self.eyes_btn.setChecked(False)
            self.eyes_btn.blockSignals(False)

    def _on_eyes_toggled(self, checked):
        if checked:
            self.source.set_alpha_level(1.0)
            self.alpha_slider.blockSignals(True)
            self.alpha_slider.setValue(100)
            self.alpha_slider.blockSignals(False)
            self.eyes_btn.setText("模拟睁眼")
        else:
            self.source.set_alpha_level(0.2)
            self.alpha_slider.blockSignals(True)
            self.alpha_slider.setValue(20)
            self.alpha_slider.blockSignals(False)
            self.eyes_btn.setText("模拟闭眼")

    # ---- 数据流 ----
    def _tick(self):
        # 累积小数样本，保证平均读取速率 = sample_rate（50ms@250Hz 应读 12.5 样本/次）
        self._frac += self.cfg.sample_rate * self.cfg.viz.refresh_ms / 1000.0
        n = int(self._frac)
        self._frac -= n
        if n <= 0:
            return
        chunk = self.source.read(n)
        filtered = self.dsp.process(chunk)
        self.waveform.push(filtered)

        self._tick_count += 1
        if self._tick_count % self._analysis_every == 0:
            self._run_analysis()

    def _run_analysis(self):
        hist = self.waveform.hist
        n_win = int(self.cfg.analysis.window_seconds * self.cfg.sample_rate)
        if hist.shape[1] < n_win:
            return  # 等窗口攒满一个 analysis 窗再算，避免短窗导致的噪声指标
        data = hist[:, -n_win:]

        metrics = self.analyzer.analyze(data)

        # 显示层 EMA 平滑（不改变分析器输出的原始指标）
        disp = dict(metrics)
        disp["faa"] = self._ema("faa", metrics["faa"])
        disp["valence"] = self._ema("valence", metrics["valence"])
        disp["arousal"] = self._ema("arousal", metrics["arousal"])
        self.emotion.update_metrics(disp)

        ch_idx = max(0, min(self.ch_selector.currentIndex(), data.shape[0] - 1))
        freqs, psd = welch_psd(data[ch_idx], self.cfg.sample_rate)
        self.spectrum.update_psd(freqs, psd)

        if metrics.get("faa_ok"):
            self.info_label.setText(f"FAA {disp['faa']:+.3f}  "
                                    f"alpha(枕区) {metrics.get('alpha_occipital', 0):.1f} µV²")
        else:
            self.info_label.setText(metrics.get("note", ""))

    def _ema(self, key, value, alpha=0.3):
        if value is None:
            return None
        prev = self._ema_state.get(key)
        if prev is None:
            self._ema_state[key] = value
            return value
        self._ema_state[key] = alpha * value + (1 - alpha) * prev
        return self._ema_state[key]

    def closeEvent(self, event):
        self.timer.stop()
        self.source.stop()
        super().closeEvent(event)


def run_app(cfg, source):
    """创建 QApplication、启动窗口并进入事件循环。"""
    app = QApplication.instance() or QApplication(sys.argv)
    setup_theme()
    source.start()
    win = MainWindow(cfg, source)
    win.resize(1280, 800)
    win.show()
    sys.exit(app.exec())
