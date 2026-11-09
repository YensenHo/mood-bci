# eegscope — 实时脑电可视化 + 情绪分析

跨平台（Windows / macOS / Linux）的可穿戴脑电采集软件。支持 RT-BCI EXP-8CH 采集板
（ADS1299），单板 16 通道 / 4 板 64 通道，实时波形、频谱、脑地形图、时频图、ERP，
情绪指标（FAA 额叶偏侧化 + 五频带功率）+ 电极接触检测。

## 一、安装

依赖（Python 3.10+）：

| 平台 | 安装命令 |
|---|---|
| Windows | 双击 `install.bat`，或命令行 `install.bat` |
| macOS / Linux | `bash install.sh` |

脚本会创建 `.venv` 虚拟环境并装好依赖（走清华镜像加速）。

## 二、Windows 必读：STM32 驱动

RT-BCI 板是 STM32 USB 虚拟串口（VCP）。**Windows 默认不认这个设备**，必须先装
STMicroelectronics 的 VCP 驱动，板子才会出现在设备管理器里变成 `COM3/COM4/...`：

1. 去 st.com 搜 "STM32 Virtual COM Port Driver"，下载并安装（`VCP_V1.x.x_Setup.exe`）；
2. 插上板子 → 设备管理器「端口(COM 和 LPT)」里应看到 `STMicroelectronics Virtual COM Port`。

macOS / Linux 自带 CDC 驱动，无需额外安装（macOS 显示为 `/dev/cu.usbmodemXXX`）。

## 三、运行

| 功能 | Windows | macOS / Linux |
|---|---|---|
| 16 通道 GUI | `.venv\Scripts\python main.py config.yaml` | `.venv/bin/python main.py config.yaml` |
| 64 通道 GUI | `.venv\Scripts\python main.py config64.yaml` | `.venv/bin/python main.py config64.yaml` |
| 扫串口/校准 | `.venv\Scripts\python scripts\detect_boards.py` | `.venv/bin/python scripts/detect_boards.py` |
| 摸电极识板 | `.venv\Scripts\python scripts\identify_boards.py` | `.venv/bin/python scripts/identify_boards.py` |
| 情绪频带测试 | `.venv\Scripts\python scripts\emotion_live.py` | `.venv/bin/python scripts/emotion_live.py` |

## 四、串口自动识别（跨平台）

`config64.yaml` 里 `ports: ["AUTO"]` 表示**自动扫描 STM32 串口**，三平台通用：

- Windows → `COM3`、`COM4` …
- macOS → `/dev/cu.usbmodemXXX`
- Linux → `/dev/ttyACMx`

⚠️ 自动扫描是 **USB 枚举顺序，不一定等于物理板 1/2/3/4 的顺序**。要通道名正确
（F3/F4 等），先跑 `scripts/identify_boards.py` 摸电极识板，再把 4 个真串口名按板序
填回 `ports:`。

## 五、硬件接法速记（RT-BCI EXP-8CH）

- 每板 16 通道 = 底座板 + 上层板各 8 通道（菊花链）；4 板 = 64 通道。
- **REF（参考）**：A1 耳后 → 分 4 根接 4 块板 REF 针（共享，被动）。
- **RLD（地/右腿驱动）**：只接 1 块板，其余悬空（8 个 RLD 抢一个地电极会打架）。
- **板子 GND 针**：空着，不碰身体。
- 板子 Type-C 无 CC 检测，Mac 上须经 USB-A 侧转接。
