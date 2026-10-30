# eegscope — 实时脑电可视化 + 情绪分析桌面应用

本项目是「情绪脑电采集」的软件。两种数据源开箱即用：

- **SyntheticSource**：合成真实感脑电（无需任何硬件），用于先验证整条链路 + 软件功能。
- **OpenBCISource**：OpenBCI 兼容 ADS1299 板的串口驱动（8ch/16ch），接真实硬件用。

实时显示多通道滚动波形、频谱、情绪指标（FAA 额叶偏侧化 + 分频带功率）。接真实硬件时
只需把 `config.yaml` 的 `source.type` 改成 `openbci` 并填串口号，DSP / 分析 / 可视化
一行不用改。

---

## 目录结构

```
software/
  README.md              # 本文件
  requirements.txt       # 依赖清单
  config.yaml            # 采样率 / 通道名 / 数据源 / 滤波 / 分析 / 可视化参数
  main.py                # 入口：读 config → 造 source → 启动 GUI
  scripts/
    smoke_test.py        # headless 冒烟测试（合成源，无 GUI）
    gui_smoke.py         # GUI 无头启动冒烟测试（offscreen 平台）
    test_openbci_parser.py  # OpenBCI 帧解析器单元测试（无需硬件）
    capture_serial.py    # 抓串口原始字节，验证真实硬件协议
  eegscope/
    __init__.py
    config.py            # 读并校验 config.yaml（dataclass）
    sources/
      __init__.py        # make_source() 工厂 —— 真实数据源在此注册
      base.py            # DataSource 抽象基类（start/stop/read + sample_rate/n_channels）
      synthetic.py       # SyntheticSource 合成真实感脑电
      openbci.py         # OpenBCISource OpenBCI 兼容板串口驱动（8ch/16ch Daisy）
    dsp.py               # 信号链：带通 0.5-45Hz + 50Hz 陷波 + 去直流/去趋势（在线带状态）
    analysis.py          # FAA + 分频带功率（Welch），含 F3/F4 缺失降级
    viz.py               # pyqtgraph 面板：堆叠波形 / 频谱 / 情绪指标（暗色主题）
    app.py               # Qt 主窗口 + 定时器，把 source→dsp→analysis→viz 串起来
```

## 数据流

```
DataSource(合成/真实) --read(n)--> SignalChain(带通+陷波+去趋势) --已滤波--> 波形滚动
                                                    └--> EmotionAnalyzer(FAA+频带功率) --> 情绪面板
                                                    └--> Welch PSD --> 频谱面板
```

## 环境安装（国内网络，务必用清华镜像 + 隔离环境）

```bash
cd ~/mood_BCI/software

# 1) 建干净 venv（用 miniconda 的 3.13；绝不用 openmmlab 的 3.8、不用系统 python3）
/opt/miniconda3/bin/python3 -m venv .venv

# 2) 清华镜像装依赖
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    pyqtgraph PySide6 numpy scipy PyYAML pyserial
```

> 运行任何 python 时**必须清空 PYTHONPATH**（否则 Hermes 的 venv 会污染 import）：

```bash
env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
    .venv/bin/python main.py
```

## 运行

```bash
cd ~/mood_BCI/software
env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
    .venv/bin/python main.py
```

默认用合成数据源启动，16 通道滚动波形 + 频谱 + 情绪指标面板。UI 上（合成源专属）：

- **alpha 幅度旋钮**：0=睁眼（alpha 弱）↔ 100=闭眼（alpha 强）。
- **「模拟闭眼」按钮**：一键让 alpha 突然增大，用来验证整条链路是通的
  （项目 HARNESS 的链路自检指标：**闭眼 alpha 上升**）。
- **PSD 通道**：下拉选择频谱面板看哪个通道的功率谱。
- **暂停/继续**：暂停定时器（数据流停）。

真实硬件（openbci 源）下，alpha 旋钮和「模拟闭眼」按钮自动隐藏。

## config.yaml 说明

| 键 | 含义 | 默认 |
|---|---|---|
| `sample_rate` | 采样率（Hz） | 250（ADS1299 典型值） |
| `channels` | 通道名列表，**通道数 = 列表长度**，改这里即可增减通道 | 16ch（见下） |
| `source.type` | 数据源类型 | `synthetic` |
| `source.synthetic.*` | 合成源参数（seed/alpha 幅度/眨眼率/噪声/工频等） | — |
| `source.openbci.port` | 串口设备（如 `/dev/cu.usbserial-XXXX`） | 空 |
| `source.openbci.baudrate` | 波特率 | 115200 |
| `source.openbci.n_channels` | 8 或 16（16=Daisy，每通道有效 125Hz） | 16 |
| `source.openbci.gain` | PGA 增益（影响 µV 缩放） | 24 |
| `source.openbci.scale_uv` | 0=按 gain 自动算；>0 直接覆盖 µV/count | 0 |
| `dsp.bandpass_hz` | 带通范围 | [0.5, 45] |
| `dsp.notch_hz` | 陷波频率 | 50（中国工频） |
| `analysis.bands` | 分频带定义（Hz） | delta/theta/alpha/beta/gamma |
| `analysis.faa_channels` | FAA 所需两通道 | [F3, F4] |
| `viz.*` | 波形窗长 / 刷新周期 / 通道显示间距 | 5s / 50ms / 80µV |

**默认 16 通道布局（10-20）**：
- 8ch 核心（项目定）：`F3 F4 F7 F8 T7 T8 O1 O2`
- 中线补充：`Fz Cz Pz`
- 对称补充：`Fp1 Fp2`、`C3 C4`、`Oz`

**弹针 3 电极场景**（2 EEG + 1 参考）：把 `channels` 改成 `["F3", "C3", "REF"]`，
应用照常运行；因缺 F4，FAA 自动降级显示「需 F3/F4」，其余频带功率正常。

## 信号链约束（对应 HARNESS M2）

- ✅ 带通 0.5–45 Hz（`scipy.signal.butter` 4 阶 sos）
- ✅ 50 Hz 陷波（中国工频，`scipy.signal.iirnotch`）
- ✅ 去直流 / 去趋势（逐通道线性 detrend）
- 在线滤波用带状态的 `sosfilt` / `lfilter`，避免分块处理边界跳变。

## 情绪指标（单被试辅助指标）

- **FAA = ln(P_F4) − ln(P_F3)**：效价辅助指标（项目约定：正=偏正性、负=偏负性）。
  P_F4 / P_F3 为 F4 / F3 通道在 alpha 频带的功率。仅当 F3、F4 同时存在才计算，
  缺失时优雅降级（不报错，显示「需 F3/F4」）。
- **分频带功率** delta(0.5-4)/theta(4-8)/alpha(8-13)/beta(13-30)/gamma(30-45)，
  以及 beta/alpha 比值、枕区 alpha，作为唤醒度/投入度辅助显示。
- 效价/唤醒度二维指示点：效价 ← FAA（tanh 映射到 [-1,1]），唤醒度 ← beta/alpha 比值。

> 注意：合成数据没有真实情绪内容，FAA 在 alpha 较弱（睁眼）时会受 1/f 噪声影响而
> 抖动（这正是真实 EEG 的已知现象——额叶 alpha 弱时 FAA 不可靠）；点「模拟闭眼」让
> alpha 增强后，FAA 会回落到接近 0 的稳定中性值。情绪面板在攒满一个分析窗（默认 2s）
> 后才开始出数。

## 冒烟验证（无 GUI）

```bash
cd ~/mood_BCI/software
env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
    .venv/bin/python scripts/smoke_test.py
```

覆盖：5 秒数据形状/无 NaN、DSP 滤波、50Hz 陷波自检、FAA + 分频带功率、
闭眼 alpha 上升、F3/F4 缺失降级。全部通过 exit 0。

GUI 无头启动冒烟（offscreen 平台，跑 3 秒数据流 + 模拟闭眼，验证不崩）：

```bash
cd ~/mood_BCI/software
env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
    QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/gui_smoke.py
```

## 真实硬件（OpenBCI 兼容 ADS1299 板）

用户买的「16 通道 ADS1299」是 OpenBCI 兼容板，走 OpenBCI Cyton/Daisy 串口协议。
驱动 `eegscope/sources/openbci.py` 直接解析 33 字节帧，不依赖 OpenBCI 官方 GUI。

### 板子到手第一步：抓字节验证协议（重要）

克隆板波特率/帧格式/通道数可能与原版有出入，**先抓几秒原始字节确认，别瞎猜**：

```bash
# 列出串口（看采集板是哪个 /dev/cu.*）
env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
    .venv/bin/python scripts/capture_serial.py

# 抓 3 秒看前 128 字节 hexdump（看 0xA0 帧头是否连续、33 字节边界是否对齐）
env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
    .venv/bin/python scripts/capture_serial.py /dev/cu.usbserial-XXXX -b 115200 -n 3
```

抓到字节后确认三件事再改 `config.yaml`：① 波特率（标准 115200）；② 通道数
（8 = Cyton / 16 = Daisy，看样本计数 bit0 是否区分主副板）；③ 缩放（若电压量级
不对，用 `scale_uv` 覆盖）。

### 接入运行

1. `config.yaml`：`source.type` 改成 `openbci`，填 `openbci.port`（串口号）、
   `n_channels`（8 或 16）、`gain`。
2. 运行 `main.py`（同合成源，见上文「运行」）。16ch Daisy 会自动把有效采样率
   调到 125Hz（`main.py` 会打印一行「采样率以数据源为准」）。

### 协议（备忘）

- 每帧 33 字节：`0xA0` 帧头 + 样本计数 + 8×3 字节 24bit 有符号通道值 + 6 字节辅助
  + 帧尾 `0xCx`。
- 缩放：`µV = raw × (4.5e6 / (gain × 2^23))`，gain 24 → 0.02235 µV/count。
- 16ch Daisy：样本计数 bit0 区分主（ch1-8）/副（ch9-16）板，每通道有效 125Hz。
- 帧解析器（`FrameScanner`）带失步重同步，帧头/帧尾不符自动重对齐。

### 驱动单元测试（无需硬件）

```bash
env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
    .venv/bin/python scripts/test_openbci_parser.py
```

覆盖：µV 缩放、24bit 符号扩展、增益缩放、帧头扫描、失步重同步、8/16 通道采样率。
