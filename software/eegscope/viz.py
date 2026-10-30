"""pyqtgraph 可视化面板：堆叠波形 + 频谱 + 情绪指标 + 脑地形图 + 信号质量（暗色主题）。"""
from __future__ import annotations

from collections import deque

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QPolygonF
from PySide6.QtWidgets import (QComboBox, QGraphicsPolygonItem, QHBoxLayout,
                               QLabel, QVBoxLayout, QWidget)
from scipy.interpolate import griddata

from .analysis import band_power_map, welch_psd

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

# 10-20 电极 2D 坐标（单位 = 头部半径，x 右为正 / y 前(额)为正，额在上枕在下）。
# 仅为可视化近似（非溯源定位），相对布局正确即可：额在上、枕在下、奇左偶右、z 中线。
CHANNEL_2D = {
    "Fp1": (-0.40, 0.75), "Fp2": (0.40, 0.75),
    "F7": (-0.72, 0.42), "F3": (-0.48, 0.52), "Fz": (0.0, 0.60),
    "F4": (0.48, 0.52), "F8": (0.72, 0.42),
    "T7": (-0.88, 0.08), "C3": (-0.60, 0.10), "Cz": (0.0, 0.10),
    "C4": (0.60, 0.10), "T8": (0.88, 0.08),
    "Pz": (0.0, -0.45),
    "O1": (-0.40, -0.72), "Oz": (0.0, -0.78), "O2": (0.40, -0.72),
    # 备用扩展（默认 16ch 不含，但 config 若含这些名字就用得上）
    "P3": (-0.52, -0.42), "P4": (0.52, -0.42), "P7": (-0.70, -0.35), "P8": (0.70, -0.35),
}


def _norm_xy(head_xy):
    """把坐标表键统一成大写，便于大小写不敏感的通道名查找。"""
    return {str(k).upper(): v for k, v in head_xy.items()}



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


# =====================================================================
# 脑地形图
# =====================================================================

def topo_interpolate(channel_names, values, grid_n=64, radius=1.0, head_xy=None):
    """把各通道标量值插值到头部俯视网格（供脑地形图热力图）。

    参数
    ----
    channel_names : list[str]  与 values 一一对应的通道名。
    values        : array-like 每通道标量（如某频带功率），长度 = len(channel_names)。
    grid_n        : 网格边长（grid_n × grid_n）。
    radius        : 头部半径（坐标单位，默认 1）。
    head_xy       : 通道 → (x, y) 坐标表，默认用 `CHANNEL_2D`。

    返回
    ----
    (grid, extent)
      grid   : (grid_n, grid_n) float。头部圆内为 `griddata` linear 插值结果，
               圆外为 np.nan（mask）。有效点 < 3 时整体为 NaN（不抛异常）。
      extent : (x0, x1, y0, y1)，网格在数据坐标中的范围（x0<x1, y0<y1）。
               约定 grid 第 0 行对应 y1（头顶/额），末行对应 y0（枕）。
    """
    if head_xy is None:
        head_xy = CHANNEL_2D
    head_xy = _norm_xy(head_xy)
    pts, vals = [], []
    for i, nm in enumerate(channel_names):
        key = str(nm).upper()
        if key in head_xy and i < len(values):
            pts.append(head_xy[key])
            vals.append(float(values[i]))
    r = 1.15 * radius
    x = np.linspace(-r, r, grid_n)
    y = np.linspace(r, -r, grid_n)  # 下行：让 grid 第 0 行 = 头顶
    gx, gy = np.meshgrid(x, y)
    if len(pts) >= 3:
        zi = griddata(np.array(pts), np.array(vals, dtype=np.float64),
                      (gx, gy), method="linear")
    else:
        zi = np.full_like(gx, np.nan)
    inside = (gx ** 2 + gy ** 2) <= (radius ** 2)
    grid = np.where(inside, zi, np.nan)
    extent = (float(x[0]), float(x[-1]), float(y[-1]), float(y[0]))
    return grid, extent


def _topo_lut(cmap_name="magma"):
    """取 pyqtgraph 内置 colormap 的 256 级 RGB LUT（uint8, shape (256, 3)）。"""
    lut = np.asarray(pg.colormap.get(cmap_name).getLookupTable(nPts=256))
    if lut.shape[-1] >= 4:
        lut = lut[:, :3]
    if lut.dtype != np.uint8:
        lut = np.clip(lut * 255, 0, 255).astype(np.uint8)
    return lut


def _grid_to_rgba(grid, cmap_name="magma"):
    """把 [0,1] 的插值网格转成 RGBA uint8 图（NaN → 全透明）。"""
    lut = _topo_lut(cmap_name)
    n = lut.shape[0]
    valid = ~np.isnan(grid)
    idx = np.clip(np.nan_to_num(grid, nan=0.0), 0.0, 1.0)
    idx = np.clip((idx * (n - 1)).astype(int), 0, n - 1)
    rgb = lut[idx]
    alpha = np.where(valid, np.uint8(255), np.uint8(0))[..., None]
    return np.concatenate([rgb, alpha], axis=2).astype(np.uint8)


class TopographyPanel(QWidget):
    """脑地形图面板：10-20 头部俯视插值热力图（按频带选功率上色）。"""

    def __init__(self, channel_names, bands):
        super().__init__()
        self.channel_names = [str(c) for c in channel_names]
        self.bands = dict(bands)
        self.band_names = list(self.bands.keys())
        self._xy = _norm_xy(CHANNEL_2D)
        # 缓存最近一次推入的各频带「每通道功率」dict：{band: np.ndarray (n_ch,)}
        self._powers = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # 标题 + 频带下拉
        head = QHBoxLayout()
        title = QLabel("脑地形图（频带功率）")
        title.setStyleSheet("font-weight: bold; font-size: 14px; color: #e0e0e0;")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(QLabel("频带"))
        self.band_combo = QComboBox()
        self.band_combo.addItems(self.band_names)
        if "alpha" in self.band_names:
            self.band_combo.setCurrentIndex(self.band_names.index("alpha"))
        self.band_combo.currentIndexChanged.connect(lambda _: self._render_current())
        head.addWidget(self.band_combo)
        layout.addLayout(head)

        # 绘图区
        self.plot = pg.PlotWidget()
        self.plot.setAspectLocked(True)
        self.plot.hideAxis("left")
        self.plot.hideAxis("bottom")
        self.plot.setXRange(-1.25, 1.25, padding=0)
        self.plot.setYRange(-1.25, 1.25, padding=0)
        layout.addWidget(self.plot, stretch=1)

        # 1) 热力图（最底层）
        self.img = pg.ImageItem()
        self.plot.addItem(self.img)
        # 2) 头轮廓 / 鼻子 / 耳朵
        self._draw_head()
        # 3) 通道圆点（最上层）
        self._draw_channel_dots()

    def _draw_head(self):
        """头轮廓圆 + 鼻子（额顶部小三角）+ 双耳（左右标记）。"""
        th = np.linspace(0.0, 2.0 * np.pi, 200)
        self.plot.plot(np.cos(th), np.sin(th),
                       pen=pg.mkPen((200, 200, 200), width=2))

        # 鼻子：额顶部小三角（y 正方向 = 额）
        nose = QGraphicsPolygonItem(QPolygonF([
            QPointF(-0.12, 1.0), QPointF(0.12, 1.0), QPointF(0.0, 1.16)]))
        nose.setPen(pg.mkPen((200, 200, 200), width=1))
        nose.setBrush(pg.mkBrush(200, 200, 200, 200))
        self.plot.addItem(nose)

        # 双耳：左右两侧小矩形
        for sx in (-1.0, 1.0):
            ear = QGraphicsPolygonItem(QPolygonF([
                QPointF(sx - 0.09, -0.14), QPointF(sx + 0.09, -0.14),
                QPointF(sx + 0.09, 0.14), QPointF(sx - 0.09, 0.14)]))
            ear.setPen(pg.mkPen((200, 200, 200), width=1))
            ear.setBrush(pg.mkBrush(200, 200, 200, 200))
            self.plot.addItem(ear)

    def _draw_channel_dots(self):
        xs, ys = [], []
        for nm in self.channel_names:
            key = nm.upper()
            if key in self._xy:
                x, y = self._xy[key]
                xs.append(x)
                ys.append(y)
        self.dots = self.plot.plot(xs, ys, pen=None, symbol="o",
                                   symbolSize=7, symbolBrush=(90, 200, 250),
                                   symbolPen=(20, 20, 25))

    def update_band_powers(self, powers):
        """推入 {band: np.ndarray (n_ch,)} 每通道频带功率，并渲染当前选中频带。"""
        self._powers = dict(powers)
        self._render_current()

    def _render_current(self):
        if not self._powers:
            return
        name = self.band_combo.currentText()
        arr = self._powers.get(name)
        if arr is None:
            return
        self._render(arr)

    def _render(self, powers):
        powers = np.asarray(powers, dtype=np.float64).ravel()
        # 收集在 CHANNEL_2D 中的通道值，缺失/无坐标的通道跳过（不报错）
        coords, vals = [], []
        for i, nm in enumerate(self.channel_names):
            key = nm.upper()
            if key in self._xy and i < powers.size:
                coords.append(self._xy[key])
                vals.append(float(powers[i]))
        if len(coords) < 3:
            return  # 不足 3 个可定位通道，无法 linear 插值，保持空图

        # 归一化：log10(功率) → min-max → [0,1]（对数压缩动态范围，突出空间对比）
        v = np.clip(np.asarray(vals), 1e-12, None)
        logv = np.log10(v)
        lo, hi = float(logv.min()), float(logv.max())
        norm = (logv - lo) / (hi - lo) if hi - lo > 1e-12 else np.full_like(logv, 0.5)

        grid, extent = topo_interpolate(self.channel_names, norm)
        rgba = _grid_to_rgba(grid)
        self.img.setImage(rgba)
        x0, x1, y0, y1 = extent
        self.img.setRect(QRectF(x0, y0, x1 - x0, y1 - y0))


# =====================================================================
# 信号质量
# =====================================================================

def compute_channel_quality(data, fs, *, rms_hi_bad=100.0, rms_hi_warn=60.0,
                            rms_lo_warn=1.0, flat_std=0.05, sat_uv=195.0,
                            sat_ratio=1e-3, mains_hz=50.0,
                            mains_power_warn=10.0, mains_power_bad=50.0):
    """对 (n_ch, n) 数据算每通道的软件代理质量指标，返回 list[dict]。

    每通道 4 个指标（阈值依据见各注释）：
      1. rms       ：窗口 RMS 幅度（µV）。过高=运动/肌肉伪迹，过低=电极脱落/平线。
      2. mains     ：50Hz 处 PSD 功率（µV²/Hz）。滤波后仍残留=陷波没压住/接地不良。
      3. flat      ：窗口标准差 ≈ 0（平线）。
      4. saturated ：|x| 逼近满量程 ±sat_uv 的样本占比（合成源满量程参考 ±200µV）。

    状态取最差指标：good(0) < warn(1) < bad(2)。
    阈值默认值依据：正常脑电 RMS 典型 5–50µV（合成源含眨眼时约 5–35µV），
    因此 rms>100 判「坏」、>60 判「可疑」、<1 判「可疑」（接近脱落）；
    平线阈值 0.05µV 已远低于任何生理信号；饱和阈值 195µV 贴近 ±200µV 满量程。
    """
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 1:
        data = data[None, :]
    n_ch, n = data.shape

    mains_psd = None
    if n >= 16:
        freqs, psd = welch_psd(data, fs)
        i_mains = int(np.argmin(np.abs(freqs - mains_hz)))
        mains_psd = psd[:, i_mains]

    results = []
    for i in range(n_ch):
        x = data[i]
        rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
        std = float(np.std(x)) if x.size else 0.0
        flat = std <= flat_std
        sat = float(np.mean(np.abs(x) >= sat_uv)) if x.size else 0.0
        saturated = sat > sat_ratio
        mains = float(mains_psd[i]) if mains_psd is not None else 0.0

        sev = 0
        reasons = []
        if flat:
            sev = max(sev, 2)
            reasons.append(f"平线(std={std:.3f}µV)")
        if saturated:
            sev = max(sev, 2)
            reasons.append(f"饱和({sat * 100:.1f}%|x|≥{sat_uv}µV)")
        if rms > rms_hi_bad:
            sev = max(sev, 2)
            reasons.append(f"RMS过高({rms:.0f}µV)")
        elif rms > rms_hi_warn:
            sev = max(sev, 1)
            reasons.append(f"RMS偏高({rms:.0f}µV)")
        elif rms < rms_lo_warn:
            sev = max(sev, 1)
            reasons.append(f"RMS过低({rms:.2f}µV)")
        if mains > mains_power_bad:
            sev = max(sev, 2)
            reasons.append(f"50Hz残留({mains:.1f}µV²/Hz)")
        elif mains > mains_power_warn:
            sev = max(sev, 1)
            reasons.append(f"50Hz残留({mains:.1f}µV²/Hz)")

        status = {0: "good", 1: "warn", 2: "bad"}[sev]
        results.append({
            "status": status,
            "reasons": reasons,
            "rms": rms, "std": std, "flat": flat, "saturated": saturated,
            "mains": mains,
        })
    return results


class QualityPanel(QWidget):
    """信号质量面板：一行通道标签，背景色=状态色（绿=正常 黄=可疑 红=坏）。"""

    _STATUS_COLORS = {
        "good": "#2e7d32",
        "warn": "#f9a825",
        "bad": "#c62828",
        "na": "#3a3a40",
    }

    def __init__(self, channel_names, fs):
        super().__init__()
        self.names = [str(c) for c in channel_names]
        self.fs = float(fs)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(4)

        head = QHBoxLayout()
        title = QLabel("信号质量")
        title.setStyleSheet("font-weight: bold; font-size: 14px; color: #e0e0e0;")
        head.addWidget(title)
        head.addStretch(1)
        legend = QLabel(
            '<span style="color:#4caf50">●</span> 正常&nbsp;&nbsp;'
            '<span style="color:#f9a825">●</span> 可疑&nbsp;&nbsp;'
            '<span style="color:#c62828">●</span> 坏')
        legend.setStyleSheet("color: #aaa; font-size: 12px;")
        head.addWidget(legend)
        layout.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(2)
        self._labels = []
        for nm in self.names:
            lbl = QLabel(nm)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedWidth(max(30, int(8 * len(nm))))
            lbl.setFixedHeight(24)
            lbl.setStyleSheet(
                f"background-color:{self._STATUS_COLORS['na']}; color:#e0e0e0;"
                "font-size: 11px; border-radius: 3px;")
            row.addWidget(lbl)
            self._labels.append(lbl)
        row.addStretch(1)
        layout.addLayout(row)

    def update_quality(self, data) -> None:
        """用当前窗数据刷新每通道状态色 + tooltip（命名避开 QWidget.update()）。"""
        results = compute_channel_quality(data, self.fs)
        for lbl, r in zip(self._labels, results):
            color = self._STATUS_COLORS.get(r["status"], self._STATUS_COLORS["na"])
            lbl.setStyleSheet(
                f"background-color:{color}; color:#ffffff; font-size: 11px;"
                "border-radius: 3px;")
            tip = "正常"
            if r["reasons"]:
                tip = "；".join(r["reasons"])
            lbl.setToolTip(f"RMS={r['rms']:.1f}µV · {tip}")

