"""Qt 主窗口 + 数据流编排：source → dsp → analysis → viz。"""
from __future__ import annotations

import os
import sys

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel,
                               QMainWindow, QPushButton, QSlider, QSplitter,
                               QVBoxLayout, QWidget)

from .analysis import EmotionAnalyzer, band_power_map, welch_psd
from .dsp import SignalChain
from .recorder import SessionRecorder, config_snapshot
from .viz import (EmotionPanel, QualityPanel, SpectrogramPanel, SpectrumPanel,
                  TopographyPanel, WaveformPanel, setup_theme)

# 录制输出根目录（software/recordings/，与 CWD 无关）
_RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "recordings")


class MainWindow(QMainWindow):
    def __init__(self, cfg, source):
        super().__init__()
        self.cfg = cfg
        self.source = source

        self.dsp = SignalChain(cfg.sample_rate, cfg.dsp.bandpass_hz,
                               cfg.dsp.notch_hz, cfg.dsp.notch_q)
        self.analyzer = EmotionAnalyzer(cfg.sample_rate, cfg.channels,
                                        cfg.analysis.bands, cfg.analysis.faa_channels)

        src_label = "合成数据" if getattr(source, "is_synthetic", False) else "真实硬件"
        self.setWindowTitle(f"eegscope — 实时脑电可视化 + 情绪分析（{src_label}）")
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

        # 会话录制状态（懒初始化，按下「录制」才建 SessionRecorder）
        self.recorder: SessionRecorder | None = None

    # ---- UI ----
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)

        root.addLayout(self._build_controls())

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左列：波形 + 信号质量（质量标签对齐波形通道）
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        self.waveform = WaveformPanel(
            self.source.n_channels, self.source.channel_names,
            self.cfg.viz.window_seconds, self.cfg.sample_rate,
            self.cfg.viz.channel_spacing_uv)
        self.quality = QualityPanel(self.source.channel_names, self.cfg.sample_rate)
        left_layout.addWidget(self.waveform, stretch=1)
        left_layout.addWidget(self.quality, stretch=0)
        splitter.addWidget(left)

        # 右列：频谱 + 情绪 + 脑地形图 + 时频图
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.spectrum = SpectrumPanel(self.cfg.sample_rate, self.cfg.analysis.bands)
        self.emotion = EmotionPanel(self.cfg.analysis.bands)
        self.topography = TopographyPanel(self.source.channel_names,
                                          self.cfg.analysis.bands)
        self.spectrogram = SpectrogramPanel(self.source.channel_names,
                                            self.cfg.sample_rate,
                                            window_seconds=10.0)
        right_layout.addWidget(self.spectrum, stretch=2)
        right_layout.addWidget(self.emotion, stretch=2)
        right_layout.addWidget(self.topography, stretch=3)
        right_layout.addWidget(self.spectrogram, stretch=3)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([750, 420])
        root.addWidget(splitter, stretch=1)

    def _build_controls(self):
        bar = QHBoxLayout()
        bar.setSpacing(10)

        self.start_btn = QPushButton("暂停")
        self.start_btn.setCheckable(True)
        self.start_btn.toggled.connect(self._on_start_toggled)
        bar.addWidget(self.start_btn)

        self.alpha_label = QLabel("alpha 幅度（睁眼↔闭眼）")
        bar.addWidget(self.alpha_label)
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

        # 真实硬件没有 alpha 旋钮/模拟闭眼概念，隐藏相关控件
        if not getattr(self.source, "is_synthetic", False):
            self.alpha_label.hide()
            self.alpha_slider.hide()
            self.eyes_btn.hide()

        # 会话录制 / 标记（合成与真实硬件都可用）
        self.record_btn = QPushButton("录制")
        self.record_btn.setCheckable(True)
        self.record_btn.toggled.connect(self._on_record_toggled)
        bar.addWidget(self.record_btn)

        self.marker_combo = QComboBox()
        self.marker_combo.setEditable(True)
        self.marker_combo.addItems(["刺激开始", "刺激结束", "事件", "自定义"])
        self.marker_combo.setCurrentText("刺激开始")
        self.marker_combo.setFixedWidth(120)
        bar.addWidget(self.marker_combo)

        self.mark_btn = QPushButton("标记")
        self.mark_btn.clicked.connect(self._on_mark_clicked)
        bar.addWidget(self.mark_btn)

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

    # ---- 会话录制 / 标记 ----
    def _on_record_toggled(self, checked):
        if checked:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        self.recorder = SessionRecorder(
            self.cfg.sample_rate,
            self.source.channel_names,
            source_type=type(self.source).__name__,
            config_snapshot=config_snapshot(self.cfg),
            recordings_dir=_RECORDINGS_DIR,
        )
        self.recorder.start()
        self.record_btn.setText("停止录制")
        self.info_label.setText("录制中…")

    def _stop_recording(self):
        if self.recorder is None:
            return
        out = self.recorder.stop()
        self.recorder = None
        # 复位按钮（blockSignals 防止再次触发 toggled 递归）
        self.record_btn.blockSignals(True)
        self.record_btn.setChecked(False)
        self.record_btn.blockSignals(False)
        self.record_btn.setText("录制")
        if out:
            self.info_label.setText(f"已保存: {os.path.basename(out)}")

    def _on_mark_clicked(self):
        if self.recorder is None or not self.recorder.is_recording:
            self.info_label.setText("未在录制，无法标记")
            return
        label = self.marker_combo.currentText().strip() or "事件"
        m = self.recorder.add_marker(label)
        if m is not None:
            self.info_label.setText(f"已标记 @ {m['time_s']:.1f}s: {label}")

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

        # 录制中：缓冲原始 µV 与滤波后数据
        if self.recorder is not None and self.recorder.is_recording:
            self.recorder.append(chunk, filtered)
            self.record_btn.setText(f"停止录制 {self.recorder.elapsed_seconds:.0f}s")

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

        # 脑地形图：对每个频带算「每通道频带功率」，推给面板（面板按当前选中频带渲染）
        band_powers = {}
        for bname, (blo, bhi) in self.cfg.analysis.bands.items():
            band_powers[bname] = band_power_map(data, self.cfg.sample_rate, blo, bhi)
        self.topography.update_band_powers(band_powers)

        # 时频图：取面板下拉选中的通道，喂最近 window_seconds 秒（约 10s）滤波数据
        sp_idx = self.spectrogram.selected_channel_index()
        sp_win = int(self.spectrogram.window_sec * self.cfg.sample_rate)
        sp_data = hist[sp_idx, -min(hist.shape[1], sp_win):]
        self.spectrogram.update_spectrogram(sp_data)

        # 信号质量：用当前分析窗（滤波后）逐通道打绿/黄/红
        self.quality.update_quality(data)

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
        # 若正在录制，先落盘再退出
        if self.recorder is not None and self.recorder.is_recording:
            try:
                self.recorder.stop()
            finally:
                self.recorder = None
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
