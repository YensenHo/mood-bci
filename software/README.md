# eegscope — 实时脑电可视化 + 情绪分析

跨平台（Windows / macOS / Linux）的可穿戴脑电采集软件。支持 RT-BCI EXP-8CH 采集板
（ADS1299），单板 16 通道 / 4 板 64 通道，实时波形、频谱、脑地形图、时频图、ERP，
情绪指标（FAA 额叶偏侧化 + 五频带功率）+ 电极接触检测。

## 🚀 免安装直接下载（Windows 最快部署，推荐给终端用户）

**不用装 Python、不用敲命令、不用去 st.com 找驱动。** 一个 zip，解压双击就能跑：

1. 到 GitHub Releases 下载 `eegscope-windows.zip`（打包机自动构建，见下）。
2. 解压，双击 `eegscope.exe`。
3. 弹出启动器 → 自动检查环境 → 缺 STM32 驱动就点「🛠 一键修复缺的东西」。
4. 点「▶ 启动采集软件」开始采集。

- **驱动一起打包**：只要把 STM32 VCP 驱动安装器 exe 放进 `software/drivers/`
  （见「二、Windows 必读」），打包时就会进 zip，终端用户点一下自动装，
  不用自己上 st.com（那网站要注册登录，很麻烦）。
- 录制的数据存在 exe 同目录的 `recordings/` 下。
- 要改 64 通道串口顺序：解压目录里就有 `config64.yaml`，直接改 `ports:` 即可。

**怎么得到这个 zip**（两种方式）：
- **自动（推荐）**：GitHub 仓库 tag 打 `v*`（如 `v1.0.0`），或手动触发
  `.github/workflows/build-release.yml`；Actions 会在 Windows / macOS 机器上打包，
  产物自动挂到 Release 直接下载。
- **手动**：在一台 Windows 电脑跑 `packaging\build_windows.bat`
  （macOS 用 `bash packaging/build.sh`），产物在 `dist/`。

> 下面的「零 ~ 五」是**源码版**（开发者 / 要改代码的人用），终端用户看上面这段就够了。

## 零、一键启动（最省事，推荐）

不用记任何命令，双击一下就完事：

| 平台 | 怎么启动 |
|---|---|
| Windows | 双击 `start.bat` |
| macOS / Linux | 命令行 `bash start.sh` |

脚本会自动：建虚拟环境 → 装依赖（缺了才装）→ 打开**启动器**窗口。

启动器窗口会**自己检查环境**（Python / 依赖 / 采集板 / STM32 驱动），缺啥点
「🛠 一键修复缺的东西」，然后点「▶ 启动采集软件」开始采集。

## 一、安装（手动，可选）

> 上面「零、一键启动」已经自动装好依赖，下面这段是给想手动装的人看的。

依赖（Python 3.10+）：

| 平台 | 安装命令 |
|---|---|
| Windows | 双击 `install.bat`，或命令行 `install.bat` |
| macOS / Linux | `bash install.sh` |

脚本会创建 `.venv` 虚拟环境并装好依赖（走清华镜像加速）。

## 二、Windows 必读：STM32 驱动

RT-BCI 板是 STM32 USB 虚拟串口（VCP）。**Windows 默认不认这个设备**，必须先装
STMicroelectronics 的 VCP 驱动，板子才会出现在设备管理器里变成 `COM3/COM4/...`：

1. 把驱动安装器 exe 放进 `software/drivers/`（从 st.com 搜 "STM32 Virtual COM
   Port Driver" 下载，或找板子厂家要）。见 `drivers/README.md`。
2. 打开启动器 → 「🛠 一键修复缺的东西」会自动运行这个 exe；
   或命令行 `.venv\Scripts\python scripts\check_env.py --install-driver`。
3. 装完插上板子 → 设备管理器「端口(COM 和 LPT)」里应看到 `STMicroelectronics Virtual COM Port`。

macOS / Linux 自带 CDC 驱动，无需额外安装（macOS 显示为 `/dev/cu.usbmodemXXX`）。

## 三、运行

| 功能 | Windows | macOS / Linux |
|---|---|---|
| 一键启动器（推荐）| 双击 `start.bat` | `bash start.sh` |
| 环境自检 | `.venv\Scripts\python scripts\check_env.py` | `.venv/bin/python scripts/check_env.py` |
| 环境自检+自动修复 | 同上加 `--fix` | 同上加 `--fix` |
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

## 六、远程实时采集（别人的数据实时传到我的电脑）

让别人（参与者）在他自己的电脑上测，数据**实时传到我的电脑**上看 / 录。

**数据流**：

```
参与者电脑（脑电帽+板）─► 我的电脑（监控端实时看+录）
     点「📤 上传」            python main.py config_remote.yaml
```

**参与者端（别人）**：
1. 打开采集软件，看到自己的波形（信号正常后再传）。
2. 点采集界面右上角「📤 上传」按钮，数据就实时发出去。
3. 发到哪、叫啥名：在 `config.yaml` 末尾的 `remote:` 里填
   - `upload_url`：我的电脑地址（见下面「怎么让我的电脑能被连到」）
   - `participant`：这个人叫什么（我这边会显示）

**我的监控端（我）**：
- 跑 `python main.py config_remote.yaml`，本地起个服务器等数据进来，实时显示 + 可录制。
- `config_remote.yaml` 里 `channels` **必须和参与者端一致**（数量 + 顺序），否则波形错。

**怎么让我的电脑能被别人连到**（绕不开的一步——家里电脑在路由器后面，公网直接连不进来）：

| 方式 | 说明 |
|---|---|
| 局域网（先测这个） | 两台电脑连同一个 WiFi，`upload_url` 填我的局域网 IP（如 `ws://192.168.1.5:8765`），零成本先跑通 |
| Tailscale Funnel | 我电脑装 Tailscale，免费暴露一个公网 `wss://` 地址，别人软件填这个地址即可，**对方不用装任何东西**（数据走 Tailscale 中继到我电脑，不经第三方存储） |
| frp | 一台有公网 IP 的 VPS 上跑 frps，我电脑跑 frpc 把 8765 端口暴露出去 |

**诚实数字**：8ch@250Hz ≈ 8 KB/s，64ch@125Hz ≈ 32 KB/s（带宽小得可忽略）；端到端延迟 100~500ms，够实时监控，不够闭环刺激。参与者照样要装 STM32 驱动（打包已解决）。

> 局域网 / 直连 / Tailscale 内网 IP 都要走「直连」，程序已强制绕过系统代理（`proxy=None`），
> 否则 macOS 会把流量送进系统 SOCKS 代理导致连不上。

## 七、新手调试 64 通道（图形向导，不用命令行）

给**不懂技术的人**用的：打开启动器，点「**🔧 调试向导**」，跟着三步走，64 通道自己就能调通，不用找技术：

1. **第 1 步 · 检测板子**：点一下，软件自动扫描 + 读数据，告诉你有几块板、每块板 16 个通道里哪个「打满」（电极没贴好）哪个「平线」（这条线没接）。说人话：打满 → 重贴那个电极；平线 → 检查那根线接没接。
2. **第 2 步 · 摸电极识板**：软件提示「请捏住板 X 的 F3 电极」，你照着捏，一个电极一个电极认完，**自动把 4 个串口按板序写进 `config64.yaml`**（不用手动改文件）。
3. **第 3 步 · 启动采集**：点一下，出波形，看质量面板大多绿色就成了。

> 这一步解决的是「4 块同型号板子，USB 插上后谁是物理板1/2/3/4」的顺序问题。只测情绪（FAA 只要 F3+F4）或只插 1 块板，其实可以跳过识板。

命令行版（开发者自己用）仍在 `scripts/detect_boards.py` 和 `scripts/identify_boards.py`，逻辑和向导共用同一套 `eegscope/diagnostics.py`。

## 八、ISC 跨被试同步监控（神经电影学主指标）

**场景**：N 个观众看同一部片，各自电脑开「上传」，数据实时传到监控端，算大家大脑的同步度（ISC）。同步高 = 片子这一刻"抓住"了所有人；低 = 掉点。

**监控端启动**（我的电脑，跑 `config_remote.yaml`）：

```bash
.venv/bin/python main.py --isc config_remote.yaml
```

窗口里实时显示：
- **同步度**：跨通道平均 ISC（-1~1），越大越同步。
- **各通道同步度柱状图**：哪些电极最"同步"（= 这个脑区最能被内容调动）。
- **在线人数 / 配对数**。

**参与者端**：跟远程采集一样，填好 `remote.upload_url` 指向监控端地址，点「上传」即可。ISC 计算在监控端 `eegscope/isc.py`（成对 Pearson 相关），数据层 `remote.py` 支持多人（每参与者一个队列）。

> 对齐假设：所有参与者用同一硬件（同采样率）、同一时间点开播，按"最新样本对齐"。若起播时间差很大，需要引入片段时间码做精确对齐（后续）。



