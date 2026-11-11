"""pyqtgraph 可视化面板：堆叠波形 + 频谱 + 情绪指标 + 脑地形图 + 信号质量（暗色主题）。"""
from __future__ import annotations

from collections import deque

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QPolygonF
from PySide6.QtWidgets import (QComboBox, QGraphicsPolygonItem, QGridLayout,
                               QHBoxLayout, QLabel, QVBoxLayout, QWidget)
from scipy.interpolate import griddata
from scipy.signal import spectrogram

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

# 10-20（扩展 10-10）电极 2D 坐标（单位 = 头部半径，x 右为正 / y 前(额)为正，额在上枕在下）。
# 仅为可视化近似（非溯源定位），相对布局正确即可：额在上、枕在下、奇左偶右、z 中线。
CHANNEL_2D = {
    # 额极
    "Fp1": (-0.40, 0.75), "Fpz": (0.00, 0.80), "Fp2": (0.40, 0.75),
    # 额前
    "AF7": (-0.66, 0.60), "AF3": (-0.40, 0.66),
    "AF4": (0.40, 0.66), "AF8": (0.66, 0.60),
    # 额
    "F7": (-0.72, 0.42), "F5": (-0.60, 0.50), "F3": (-0.48, 0.52),
    "F1": (-0.24, 0.56), "Fz": (0.00, 0.60), "F2": (0.24, 0.56),
    "F4": (0.48, 0.52), "F6": (0.60, 0.50), "F8": (0.72, 0.42),
    # 额颞 / 额中央
    "FT7": (-0.84, 0.26), "FC5": (-0.66, 0.34), "FC3": (-0.50, 0.36),
    "FC1": (-0.24, 0.38), "FCz": (0.00, 0.39), "FC2": (0.24, 0.38),
    "FC4": (0.50, 0.36), "FC6": (0.66, 0.34), "FT8": (0.84, 0.26),
    # 颞 / 中央
    "T7": (-0.88, 0.08), "C5": (-0.72, 0.12), "C3": (-0.60, 0.10),
    "C1": (-0.28, 0.12), "Cz": (0.00, 0.10), "C2": (0.28, 0.12),
    "C4": (0.60, 0.10), "C6": (0.72, 0.12), "T8": (0.88, 0.08),
    # 颞顶 / 顶中央
    "TP7": (-0.84, -0.14), "CP5": (-0.66, -0.14), "CP3": (-0.50, -0.16),
    "CP1": (-0.24, -0.16), "CPz": (0.00, -0.16), "CP2": (0.24, -0.16),
    "CP4": (0.50, -0.16), "CP6": (0.66, -0.14), "TP8": (0.84, -0.14),
    # 顶
    "P7": (-0.70, -0.35), "P5": (-0.60, -0.40), "P3": (-0.52, -0.42),
    "P1": (-0.24, -0.44), "Pz": (0.00, -0.45), "P2": (0.24, -0.44),
    "P4": (0.52, -0.42), "P6": (0.60, -0.40), "P8": (0.70, -0.35),
    # 顶枕
    "PO7": (-0.70, -0.58), "PO5": (-0.56, -0.62), "PO3": (-0.42, -0.65),
    "POz": (0.00, -0.67), "PO4": (0.42, -0.65), "PO6": (0.56, -0.62),
    "PO8": (0.70, -0.58),
    # 枕 / 耳下枕底(CB)
    "O1": (-0.40, -0.72), "Oz": (0.00, -0.78), "O2": (0.40, -0.72),
    "CB1": (-0.62, -0.86), "CB2": (0.62, -0.86),
    # 空位（第 64 通道，不接电极；画在头外，不参与头内插值）
    "NC": (0.00, -1.15),
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

        self.title_label = QLabel("情绪指标（单被试辅助指标）")
        self.title_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #e0e0e0;")
        layout.addWidget(self.title_label)

        self.faa_label = QLabel("FAA = --")
        f = QFont("Menlo")
        f.setPointSize(22)
        f.setBold(True)
        self.faa_label.setFont(f)
        self.faa_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.faa_label.setStyleSheet("color: #888;")
        layout.addWidget(self.faa_label)

        # 额叶 theta 不对称（效价备选指标，部分研究用 theta 更敏感）
        self.theta_label = QLabel("θ不对称 = --")
        self.theta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.theta_label.setStyleSheet("color: #888; font-size: 13px;")
        layout.addWidget(self.theta_label)

        # 投入度（中央区 beta/alpha，HARNESS 指标表）+ 枕顶低频（唤醒度相关）
        self.engagement_label = QLabel("投入度 = --")
        self.engagement_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.engagement_label.setStyleSheet("color: #888; font-size: 13px;")
        layout.addWidget(self.engagement_label)

        self.po_lf_label = QLabel("枕顶低频 = --")
        self.po_lf_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.po_lf_label.setStyleSheet("color: #888; font-size: 13px;")
        layout.addWidget(self.po_lf_label)

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
        # 标题：已校准则标 z-score
        if metrics.get("_calibrated"):
            self.title_label.setText("情绪指标（单被试辅助指标 · 已校准 z-score）")
        else:
            self.title_label.setText("情绪指标（单被试辅助指标）")

        # FAA
        faa = metrics.get("faa")
        if metrics.get("faa_ok") and faa is not None:
            color = "#4caf50" if faa >= 0 else "#f44336"
            self.faa_label.setText(f"FAA = {faa:+.3f}")
        else:
            color = "#888"
            self.faa_label.setText("需 F3/F4")
        self.faa_label.setStyleSheet(f"color: {color};")

        # 额叶 theta 不对称
        theta_asym = metrics.get("theta_asymmetry")
        if theta_asym is not None:
            tcolor = "#4caf50" if theta_asym >= 0 else "#f44336"
            self.theta_label.setText(f"θ不对称 = {theta_asym:+.3f}")
            self.theta_label.setStyleSheet(f"color: {tcolor}; font-size: 13px;")
        else:
            self.theta_label.setText("θ不对称 = --")
            self.theta_label.setStyleSheet("color: #888; font-size: 13px;")

        # 投入度（中央区 beta/alpha）+ 枕顶低频
        eng = metrics.get("engagement")
        if eng is not None:
            self.engagement_label.setText(f"投入度 = {eng:.2f}")
            self.engagement_label.setStyleSheet("color: #bbb; font-size: 13px;")
        else:
            self.engagement_label.setText("投入度 = --")
            self.engagement_label.setStyleSheet("color: #888; font-size: 13px;")
        po_lf = metrics.get("parieto_occipital_lf")
        if po_lf is not None:
            self.po_lf_label.setText(f"枕顶低频 = {po_lf:.1f}")
            self.po_lf_label.setStyleSheet("color: #bbb; font-size: 13px;")
        else:
            self.po_lf_label.setText("枕顶低频 = --")
            self.po_lf_label.setStyleSheet("color: #888; font-size: 13px;")

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
        matched_idx, vals = [], []
        for i, nm in enumerate(self.channel_names):
            key = nm.upper()
            if key in self._xy and i < powers.size:
                matched_idx.append(i)
                vals.append(float(powers[i]))
        if len(matched_idx) < 3:
            return  # 不足 3 个可定位通道，无法 linear 插值，保持空图

        # 归一化：log10(功率) → min-max → [0,1]（对数压缩动态范围，突出空间对比）
        v = np.clip(np.asarray(vals), 1e-12, None)
        logv = np.log10(v)
        lo, hi = float(logv.min()), float(logv.max())
        norm_matched = (logv - lo) / (hi - lo) if hi - lo > 1e-12 else np.full_like(logv, 0.5)

        # 映射回「全通道长度」对齐 channel_names（未匹配/无坐标通道留 NaN，
        # topo_interpolate 只读有坐标的通道，自动跳过 NaN）。之前把「已匹配通道」
        # 的归一化结果直接按全通道下标传进去，遇到 NC 占位通道就会整体错位。
        norm = np.full(len(self.channel_names), np.nan)
        norm[matched_idx] = norm_matched

        grid, extent = topo_interpolate(self.channel_names, norm)
        rgba = _grid_to_rgba(grid)
        self.img.setImage(rgba)
        x0, x1, y0, y1 = extent
        self.img.setRect(QRectF(x0, y0, x1 - x0, y1 - y0))


# =====================================================================
# 信号质量
# =====================================================================

def compute_channel_quality(data, fs, *, full_scale_uv=None,
                            rms_lo_warn=1.0, flat_std=0.05,
                            railed_rms=150000.0, sat_uv=195.0,
                            sat_ratio=1e-3, mains_hz=50.0):
    """接触质量检测（傻子化）：只判「没接上 / 打满 / 良好」三态。

    - 没接上（平线）：rms≈0 且 std≈0，电极没贴到头皮。
    - 接触不良（打满）：rms 逼近满量程 或 |x| 撞满量程，电极悬空/开路。
    - 良好：其余（电极贴着头皮，有信号）。

    ⚠️ rms 偏高（50Hz 工频、运动伪迹）不再判「坏」——那是有信号但噪声大，
    是滤波的事，不是「接触」的事。满量程用 full_scale_uv（真实板 187500µV），
    给定后据此重算饱和阈值；不给定用合成源口径（sat_uv 默认 195µV）。
    """
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 1:
        data = data[None, :]
    n_ch, n = data.shape
    if full_scale_uv is not None:
        sat_uv = 0.95 * float(full_scale_uv)

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
        # 平线 = 既无波动又无幅度（真没接上）；打满的通道 std≈0 但 rms 巨大，不算平线
        flat = (std <= flat_std) and (rms < rms_lo_warn)
        sat = float(np.mean(np.abs(x) >= sat_uv)) if x.size else 0.0
        saturated = sat > sat_ratio
        mains = float(mains_psd[i]) if mains_psd is not None else 0.0

        sev = 0
        reasons = []
        if flat:
            sev = 2
            reasons.append("没接上（无信号）")
        elif saturated or rms > railed_rms:
            sev = 2
            if saturated:
                reasons.append(f"接触不良（信号打满 {sat * 100:.0f}%）")
            else:
                reasons.append(f"接触不良（信号过大 rms={rms:.0f}µV）")
        # 其余 = 良好（贴着有信号；rms 高只是 50Hz/运动噪声，不算接触问题）

        status = {0: "good", 1: "warn", 2: "bad"}[sev]
        results.append({
            "status": status,
            "reasons": reasons,
            "rms": rms, "std": std, "flat": flat, "saturated": saturated,
            "mains": mains,
        })
    return results


class QualityPanel(QWidget):
    """电极接触检测面板（傻子化设计）。

    - 顶部大字总结：「✅ 64 个电极全部接触良好」或「⚠️ 5 个需调整」
    - 第二行大白话指令：「请调整电极：F3、F4、O1」
    - 通道用网格排布（64 通道 = 16 列 × 4 行），背景色=状态色，悬停看原因
    """

    _STATUS_COLORS = {
        "good": "#2e7d32",
        "warn": "#f9a825",
        "bad": "#c62828",
        "na": "#3a3a40",
    }
    _STATUS_TEXT = {"good": "良好", "warn": "异常", "bad": "接触不良"}

    def __init__(self, channel_names, fs, full_scale_uv=None):
        super().__init__()
        self.names = [str(c) for c in channel_names]
        self.fs = float(fs)
        self.full_scale_uv = full_scale_uv

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(4)

        # 标题 + 图例
        head = QHBoxLayout()
        title = QLabel("电极接触检测")
        title.setStyleSheet("font-weight: bold; font-size: 14px; color: #e0e0e0;")
        head.addWidget(title)
        head.addStretch(1)
        legend = QLabel(
            '<span style="color:#4caf50">●</span> 良好&nbsp;&nbsp;'
            '<span style="color:#f9a825">●</span> 异常&nbsp;&nbsp;'
            '<span style="color:#c62828">●</span> 接触不良')
        legend.setStyleSheet("color: #aaa; font-size: 12px;")
        head.addWidget(legend)
        layout.addLayout(head)

        # 总体状态（大字总结）
        self.summary = QLabel("等待数据…")
        self.summary.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #999; padding: 2px;")
        layout.addWidget(self.summary)

        # 请调整清单（大白话指令）
        self.advice = QLabel("")
        self.advice.setWordWrap(True)
        self.advice.setStyleSheet("font-size: 12px; color: #f9a825; padding: 2px;")
        layout.addWidget(self.advice)

        # 通道网格（多行，适配 64 通道）
        grid = QGridLayout()
        grid.setSpacing(2)
        self._labels = []
        n = len(self.names)
        cols = 16 if n > 16 else max(1, n)
        for i, nm in enumerate(self.names):
            lbl = QLabel(nm)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedHeight(22)
            lbl.setMinimumWidth(34)
            lbl.setStyleSheet(
                f"background-color:{self._STATUS_COLORS['na']}; color:#e0e0e0;"
                "font-size: 10px; border-radius: 3px;")
            grid.addWidget(lbl, i // cols, i % cols)
            self._labels.append(lbl)
        layout.addLayout(grid)
        layout.addStretch(1)

    def update_quality(self, data) -> None:
        """用当前窗数据刷新总结 + 每通道状态（命名避开 QWidget.update()）。"""
        results = compute_channel_quality(data, self.fs, full_scale_uv=self.full_scale_uv)
        good = warn = bad = 0
        bad_names, warn_names = [], []
        for lbl, r, nm in zip(self._labels, results, self.names):
            color = self._STATUS_COLORS.get(r["status"], self._STATUS_COLORS["na"])
            lbl.setStyleSheet(
                f"background-color:{color}; color:#ffffff; font-size: 10px;"
                "border-radius: 3px;")
            tip = self._STATUS_TEXT.get(r["status"], "未知")
            if r["reasons"]:
                tip += "：" + "、".join(r["reasons"])
            lbl.setToolTip(f"{nm} · RMS={r['rms']:.1f}µV · {tip}")
            if r["status"] == "good":
                good += 1
            elif r["status"] == "bad":
                bad += 1
                bad_names.append(nm)
            else:
                warn += 1
                warn_names.append(nm)

        total = good + warn + bad
        if total == 0:
            self.summary.setText("等待数据…")
            self.summary.setStyleSheet(
                "font-size: 14px; font-weight: bold; color: #999; padding: 2px;")
            self.advice.setText("")
            return
        need = bad_names + warn_names
        if not need:
            self.summary.setText(f"✅ {total} 个电极全部接触良好")
            self.summary.setStyleSheet(
                "font-size: 14px; font-weight: bold; color: #4caf50; padding: 2px;")
            self.advice.setText("")
        else:
            self.summary.setText(
                f"⚠️ {good}/{total} 良好 · {warn} 异常 · {bad} 接触不良")
            self.summary.setStyleSheet(
                "font-size: 14px; font-weight: bold; color: #f9a825; padding: 2px;")
            self.advice.setText("请调整电极：" + "、".join(need))


# =====================================================================
# 时频图
# =====================================================================

class SpectrogramPanel(QWidget):
    """时频图面板：单个选定通道最近一段滤波数据的 STFT 时频谱。

    - x 轴 = 时间（秒，最新样本在 0，与波形面板一致，滚动窗为最近
      `window_seconds` 秒）；y 轴 = 频率（0~`freq_max`，默认 45Hz）；
      颜色 = log10 功率谱密度（µV²/Hz），用 magma colormap（与脑地形图一致）。
    - 顶部自带通道下拉，选择看哪个通道的时频图。
    - `update_spectrogram(channel_data_1d, fs=None)` 接收该通道最近一段滤波数据（1D），
      用 `scipy.signal.spectrogram`（STFT 幅度平方，mode='psd'）算时频谱并刷新。
      面板只负责计算+渲染，喂数据由 `app._run_analysis` 按约 500ms 节奏驱动。
      方法名带 `_spectrogram` 后缀以避开 `QWidget.update()`（沿用本项目既有约定）。
    - 显示电平用**固定** log10 范围 `_LEVELS`（不逐帧自适应归一化），这样
      「模拟闭眼 → alpha 功率上升」会表现为 10Hz 横带真实变亮，而非被归一化抵消。
    """

    _LEVELS = (-3.0, 3.0)   # log10(µV²/Hz) 显示范围（固定）
    _EPS = 1e-12

    def __init__(self, channel_names, fs, window_seconds=10.0, freq_max=45.0):
        super().__init__()
        self.names = [str(c) for c in channel_names]
        self.fs = float(fs)
        self.window_sec = float(window_seconds)
        self.freq_max = float(freq_max)

        # 最近一次 STFT 结果（供测试/调试读取；全频段 + 裁剪后两份）
        self._freqs_full = None      # 全频段频率轴（0~fs/2）
        self._logpower_full = None   # 全频段 log10 功率 (n_freq, n_time)
        self._freqs = None           # 裁剪到 [0, freq_max] 的频率轴
        self._logpower = None        # 裁剪后 log10 功率 (n_freq, n_time)
        self._t = None               # 各段时间中心（相对窗口起点，秒）

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        head = QHBoxLayout()
        title = QLabel("时频图（单通道）")
        title.setStyleSheet("font-weight: bold; font-size: 14px; color: #e0e0e0;")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(QLabel("通道"))
        self.ch_combo = QComboBox()
        self.ch_combo.addItems(self.names)
        # 默认看枕区通道（合成源 alpha 最强），找不到则第 0 通道
        default = 0
        for pref in ("O1", "O2", "Oz", "OZ"):
            for i, nm in enumerate(self.names):
                if nm.upper() == pref:
                    default = i
                    break
            else:
                continue
            break
        self.ch_combo.setCurrentIndex(default)
        head.addWidget(self.ch_combo)
        layout.addLayout(head)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "时间", units="s")
        self.plot.setLabel("left", "频率", units="Hz")
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setXRange(-self.window_sec, 0.0, padding=0)
        self.plot.setYRange(0.0, self.freq_max, padding=0)
        layout.addWidget(self.plot, stretch=1)

        self.img = pg.ImageItem()
        self.img.setLookupTable(_topo_lut("magma"))
        self.plot.addItem(self.img)

        # 颜色条（log10 功率，固定范围）
        try:
            self.colorbar = pg.ColorBarItem(
                values=self._LEVELS, colorMap=pg.colormap.get("magma"),
                width=12, label="log10(µV²/Hz)")
            self.colorbar.setImageItem(self.img)
            self.plot.getPlotItem().layout.addItem(self.colorbar, 2, 3)
        except Exception:
            self.colorbar = None

    # ---- 查询 ----
    def selected_channel_index(self) -> int:
        """当前下拉选中的通道索引（越界钳制到合法范围）。"""
        i = self.ch_combo.currentIndex()
        return max(0, min(i, len(self.names) - 1))

    # ---- 数据入口 ----
    def update_spectrogram(self, channel_data_1d, fs=None) -> None:
        """用该通道最近一段滤波数据（1D）算 STFT 并刷新。

        `fs` 可选（默认用构造时采样率）；数据过短时保持上一帧图像不报错。
        """
        x = np.asarray(channel_data_1d, dtype=np.float64).ravel()
        if fs is not None:
            self.fs = float(fs)
        fs = self.fs
        n = x.size
        if n < 32:
            return  # 数据不足，保持上一帧

        nperseg = int(max(16, min(256, n)))
        noverlap = int(nperseg * 0.75)
        if noverlap >= nperseg:
            noverlap = nperseg - 1

        f, t, sxx = spectrogram(x, fs=fs, nperseg=nperseg, noverlap=noverlap,
                                detrend="constant", mode="psd")
        logp = np.log10(np.maximum(sxx, self._EPS))
        self._freqs_full = np.asarray(f, dtype=np.float64)
        self._logpower_full = np.asarray(logp, dtype=np.float64)
        self._t = np.asarray(t, dtype=np.float64)

        # 裁剪到 [0, freq_max]
        m = f <= self.freq_max
        f_c = np.asarray(f[m], dtype=np.float64)
        logp_c = logp[m, :]
        if f_c.size < 2:
            return
        self._freqs = f_c
        self._logpower = np.asarray(logp_c, dtype=np.float64)

        # 相对时间：最新样本 = 0（与波形面板一致）
        dur = n / fs
        t_rel = self._t - dur
        img = np.ascontiguousarray(logp_c[::-1, :])  # 行 0 = 最高频（画在顶部）
        self.img.setImage(img, levels=self._LEVELS, autoLevels=False)
        self.img.setRect(QRectF(float(t_rel[0]), float(f_c[0]),
                                float(t_rel[-1] - t_rel[0]),
                                float(f_c[-1] - f_c[0])))

