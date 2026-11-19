"""Raw EEG channel screening; no GUI dependency."""
import numpy as np
from .analysis import welch_psd


def compute_channel_quality(data, fs, *, full_scale_uv=None,
                            rms_lo_warn=1.0, flat_std=0.05,
                            railed_rms=150000.0, sat_uv=195.0,
                            sat_ratio=1e-3, mains_hz=50.0):
    """按原始窗口初筛信号和数据完整性异常，不诊断电极接触。"""
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 1:
        data = data[None, :]
    n_ch, n = data.shape
    if full_scale_uv is not None:
        sat_uv = 0.95 * float(full_scale_uv)
        railed_rms = 0.8 * float(full_scale_uv)

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
        flat = std <= flat_std
        sat = float(np.mean(np.abs(x) >= sat_uv)) if x.size else 0.0
        saturated = sat > sat_ratio
        zero_fraction = float(np.mean(x == 0)) if x.size else 0.0
        zero_run = _longest_run(x == 0)
        same = np.isfinite(x[1:]) & np.isfinite(x[:-1]) & (x[1:] == x[:-1])
        repeated_run = _longest_run(same) + 1 if same.size else 0
        integrity_warning = (zero_fraction >= 0.05 or
                             zero_run >= max(3, round(fs * 0.1)) or
                             repeated_run >= max(3, round(fs * 0.1)))
        mains = float(mains_psd[i]) if mains_psd is not None else 0.0

        sev = 0
        reasons = []
        if not np.isfinite(x).all():
            sev = 2
            reasons.append("数据无效")
        if saturated:
            sev = 2
            reasons.append(f"信号接近量程极限 {sat * 100:.1f}%")
        elif rms > railed_rms:
            sev = 2
            reasons.append(f"信号 RMS 接近量程 ({rms:.0f}µV)")
        if flat:
            sev = 2
            reasons.append("信号平线，原因待检查")
        if integrity_warning:
            sev = max(sev, 1)
            reasons.append(
                f"数据完整性需复核：零值 {zero_fraction * 100:.1f}% / "
                f"最长零值段 {zero_run / fs:.2f}s / 重复值平台 {repeated_run / fs:.2f}s"
            )
        # 其余 = 未检出明显异常；仍不代表接触或脑电质量已验证。

        status = {0: "good", 1: "warn", 2: "bad"}[sev]
        results.append({
            "status": status,
            "reasons": reasons,
            "rms": rms, "std": std, "flat": flat, "saturated": saturated,
            "zero_fraction": zero_fraction,
            "longest_zero_run_s": zero_run / fs,
            "longest_repeated_run_s": repeated_run / fs,
            "integrity_warning": integrity_warning,
            "mains": mains,
        })
    return results


def _longest_run(mask):
    edges = np.diff(np.r_[False, np.asarray(mask, dtype=bool), False].astype(np.int8))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return int(np.max(ends - starts)) if starts.size else 0

