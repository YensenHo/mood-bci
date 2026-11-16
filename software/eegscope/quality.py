"""Raw EEG channel screening; no GUI dependency."""
import numpy as np
from .analysis import welch_psd


def compute_channel_quality(data, fs, *, full_scale_uv=None,
                            rms_lo_warn=1.0, flat_std=0.05,
                            railed_rms=150000.0, sat_uv=195.0,
                            sat_ratio=1e-3, mains_hz=50.0):
    """按原始窗口初筛平线、非有限数据及接近量程极限，不诊断电极接触。"""
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
        mains = float(mains_psd[i]) if mains_psd is not None else 0.0

        sev = 0
        reasons = []
        if not np.isfinite(x).all():
            sev = 2
            reasons.append("数据无效")
        elif saturated or rms > railed_rms:
            sev = 2
            reasons.append(f"信号接近量程极限 {sat * 100:.1f}%" if saturated
                           else f"信号 RMS 接近量程 ({rms:.0f}µV)")
        elif flat:
            sev = 2
            reasons.append("信号平线，原因待检查")
        # 其余 = 良好（贴着有信号；rms 高只是 50Hz/运动噪声，不算接触问题）

        status = {0: "good", 1: "warn", 2: "bad"}[sev]
        results.append({
            "status": status,
            "reasons": reasons,
            "rms": rms, "std": std, "flat": flat, "saturated": saturated,
            "mains": mains,
        })
    return results

