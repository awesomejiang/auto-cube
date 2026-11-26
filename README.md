# Quanah Cube

适用于 Windows 10/11 x64 的 Quanah 魔方工具。

1. 用下文的 Windows 构建脚本生成桌面便携包，解压整个文件夹，双击 `Quanah-Cube.exe` 并确认 Windows 的管理员权限提示。
2. 在游戏里打开带 **Reset x1** 的魔方结果页。
3. 用下拉菜单添加目标词条，例如 `STR`、`STR|ALL`，第三条留空。点词条标签可移除。需要在右侧 CP 增加时也停下，就勾选 **CP 增加时停止**。清空全部词条并勾选该项可只按 CP 判断。
4. 点 **开始重洗**，在 5 秒内切回游戏。材料由游戏检查。

三个条件可以用 AFTER 中的任意顺序满足，每条实际词条只能匹配一个条件。`ALL` 表示全属性，留空表示任意词条。可选类别参考 [MathBro 的魔方计算器](https://brendonmay.github.io/cubingCalculator/)。

`ATT` 和 `MATT` 匹配百分比物攻和魔攻。冷却缩减可分别选 **CDR -1 秒** 或 **CDR -2 秒**（命令行输入 `CD1` 或 `CD2`）。冷却条件按总秒数计算，例如两个 -1 秒条件可以由一条 -2 秒满足。

Windows 本地构建还附带 `Quanah-Cube-CLI.exe`。命令行菜单如下。

- **1** 右侧 CP 增加时停止。
- **2** 命中目标词条时停止。
- **3** 命中目标词条或右侧 CP 增加时停止。

命中停止条件时会播放上扬提示音，停在结果页等你手动选 AFTER。按住 F8 或切出游戏也会停止。词条模式下，如果读数始终无法确认，程序会停止重洗。

AFTER 会逐行比对词条模板，需要补读的行用 OCR 识别。若已知词条足以排除目标，可跳过其余行。连续两帧读数一致后才会进入下一轮。

每次按键后等待约 0.2 秒，随机浮动 ±0.04 秒。实际间隔还会加上截图识别和服务器响应时间。工具识别到 `Please try again later.` 后会短暂等待，再按回车关闭提示。识别区域按参考画面的客户区比例设置。

## 构建便携包

### Windows 10/11 x64

安装 Go、带 `py` 启动器和 pip 的 Python 3、7-Zip、Node.js（22.12 或更高版本）、Rust MSVC 工具链和 Visual Studio 的 C++ 桌面开发组件。将 Go、Node.js 和 Cargo 加入 PATH。7-Zip 可安装在默认位置，也可将 `7z.exe` 或 `7zz.exe` 加入 PATH。桌面界面运行时需要 Microsoft Edge WebView2。在仓库目录打开 PowerShell 后执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

解压 `dist/Quanah-Cube-Windows-x64.zip`，打开解压目录中的 `Quanah-Cube.exe` 试读。桌面界面采用 Tauri 2、React 19、TypeScript 和 Vite。

### Linux

安装 Go、Python 3 和 pip、7-Zip（`7zz`）、curl、zip、unzip 和 sha256sum 后执行 `./build.sh`，交叉构建 Windows 命令行便携包。

两个脚本都在 `dist/` 生成 ZIP，内含 Python 3.12.10 和 Tesseract 5.4.0 英文识别运行时。

发行包已通过 ZIP 完整性检查。Windows 游戏中的识别和按键仍需实测。
