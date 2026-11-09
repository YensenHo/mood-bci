# drivers/ —— STM32 虚拟串口（VCP）驱动

RT-BCI 采集板走的是 STM32 的 USB 虚拟串口（VCP）。

- **macOS / Linux**：系统自带 CDC 驱动，**不需要**这里任何东西。
- **Windows**：默认不认 STM32 虚拟串口，板子插上会显示「未知设备」，
  必须先装 STMicroelectronics 的 VCP 驱动，板子才会变成 `COM3/COM4/...`。

## 怎么让「一键修复」能自动装驱动

把驱动安装器 **exe** 放到**本目录**（`software/drivers/`）里，启动器里的
「🛠 一键修复缺的东西」就会自动运行它。一次放好，之后每台 Windows 都能一键装。

### 从哪下载这个 exe

1. ST 官网（需注册登录）：搜 **"STM32 Virtual COM Port Driver"**（STSW-STM32102），
   下载后解压得到 `VCP_V1.x.x_Setup.exe`。
   https://www.st.com/en/development-tools/stsw-stm32102.html
2. 或者用板子厂家（RT-BCI）提供的驱动包里的安装器。

把 `.exe` 拷进本目录即可（文件名不限，启动器会自动找 `*.exe`）。

### 放进本目录后，会自动进「免安装 zip」

打包（`packaging/build.sh` / `build_windows.bat` / GitHub Actions）时会把本目录里的
`.exe` 一起打进发布 zip，终端用户解压后点「🛠 一键修复缺的东西」就能自动装，
**不用自己上 st.com 注册登录**。所以：打包发布前，先把驱动 exe 放进来。
