"""比较两份录制的原始频谱；不修改录制目录，也不假设板序相同。"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

import numpy as np
from scipy.signal import welch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_recording(folder: Path):
    meta = json.loads((folder / 'meta.json').read_text(encoding='utf8'))
    with np.load(folder / 'session.npz', allow_pickle=False) as f:
        raw = np.asarray(f['raw'], dtype=np.float64)
        fs = float(f['fs'])
    if raw.ndim != 2 or not np.isfinite(fs) or fs <= 52 or raw.shape[1] < fs * 4:
        raise ValueError('需要二维原始数据、至少 4 秒且采样率大于 52 Hz')
    if raw.shape[0] % 16:
        raise ValueError('当前对照仅支持每组 16 行的录制')
    hw = (meta.get('config', {}).get('source', {}).get('openbci') or {})
    gains = [1., 2., 4., 6., 8., 12., 24.]
    code = hw.get('gain_code', -1)
    gain = gains[code] if isinstance(code, int) and 0 <= code < len(gains) else float(hw.get('gain') or 24.)
    scale = float(hw.get('scale_uv') or 0.)
    if not np.isfinite(gain) or gain <= 0 or not np.isfinite(scale) or scale < 0:
        raise ValueError('增益或缩放配置无效')
    full_scale = scale * 2**23 if scale > 0 else 4.5e6 / gain
    return raw, fs, meta, full_scale


def compare(first: Path, second: Path):
    a, fs, ma, full_a = load_recording(first)
    b, fs_b, mb, full_b = load_recording(second)
    if a.shape[0] != b.shape[0] or fs != fs_b:
        raise ValueError('两份录制的通道数或采样率不同，不能按行对照')
    groups = []
    for start in range(0, a.shape[0], 16):
        xs = [a[start:start+16], b[start:start+16]]
        valid = np.ones(16, dtype=bool)
        for x, scale in zip(xs, (full_a, full_b)):
            valid &= np.isfinite(x).all(axis=1) & (np.std(x, axis=1) > .05)
            valid &= (np.mean(np.abs(x) >= .95 * scale, axis=1) < .001)
        powers, fractions = [], []
        for x in xs:
            if not valid.any():
                break
            f, p = welch(x[valid], fs, nperseg=int(fs * 4), axis=1)
            narrow = p[:, (f >= 24) & (f <= 26)].sum(axis=1)
            total = p[:, (f >= 1) & (f <= min(45, fs / 2))].sum(axis=1)
            powers.append(narrow)
            fractions.append(np.divide(narrow, total, out=np.full_like(total, np.nan), where=total > 0))
        row = {'file_row_group': start // 16 + 1, 'eligible_row_count': int(valid.sum()),
               'first_fraction_median': None, 'second_fraction_median': None,
               'second_over_first_narrow_power_median': None}
        if powers:
            for key, values in zip(('first_fraction_median', 'second_fraction_median'), fractions):
                finite = values[np.isfinite(values)]
                if finite.size:
                    row[key] = float(np.median(finite))
            # 单位/增益不同不能比较功率；即使配置相同，物理增益仍需核实。
            if full_a == full_b:
                ratio = np.divide(powers[1], powers[0], out=np.full_like(powers[0], np.nan), where=powers[0] > 0)
                finite = ratio[np.isfinite(ratio)]
                if finite.size:
                    row['second_over_first_narrow_power_median'] = float(np.median(finite))
        groups.append(row)
    return {'first_recorded_at': ma.get('recorded_at'), 'second_recorded_at': mb.get('recorded_at'),
            'sample_rate': fs, 'groups': groups,
            'warning': '按文件行比较；物理板序和增益未验证，不能据此确认同一设备或干扰源。排除规则不是接触或脑电质量认证。'}


def main():
    parser = argparse.ArgumentParser(description='比较两次录制的 25 Hz 原始频谱')
    parser.add_argument('first', type=Path)
    parser.add_argument('second', type=Path)
    parser.add_argument('--output', type=Path, required=True, help='保存新 JSON 的路径，不能覆盖已有文件')
    args = parser.parse_args()
    result = compare(args.first, args.second)
    # 独占创建：误操作时不得覆盖已有录制或报告。
    with args.output.open('x', encoding='utf8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(f'已写入 {args.output}')


if __name__ == '__main__':
    main()
