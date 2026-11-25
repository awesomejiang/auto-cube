# Quanah Cube

适用于 Windows 10/11 x64 的 Quanah 魔方工具。

1. 打开 [Windows x64 便携包](https://github.com/awesomejiang/auto-cube/blob/windows-build/Quanah-Cube-Windows-x64.zip)，点击 **Download raw file**，解压整个文件夹。
2. 双击 `Quanah-Cube.exe`，选 **1** 试读。按提示在 5 秒内切回魔方结果页。
3. 确认左右 CP、材料和单次消耗读数正确后，重新打开程序，选 **2** 开始。

右侧 CP 更高时会播放上扬提示音，停在结果页等你手动选 AFTER。材料不足会播放下降提示音并停止。按住 F8 或切出游戏也会停止。

每次按键后等待约 0.2 秒，随机浮动 ±0.04 秒。实际间隔还会加上截图识别和服务器响应时间。工具识别到 `Please try again later.` 后会短暂等待，再按回车关闭提示。识别区域按参考画面的客户区比例设置。

## 构建便携包

在 Linux 安装 Go、Python 3 和 pip、7-Zip（`7zz`）、curl、zip、unzip 和 sha256sum 后执行 `./build.sh`。生成的 ZIP 位于 `dist/`，包含 Python 3.12.10 和 Tesseract 5.4.0 英文识别运行时。

发行包已通过 ZIP 完整性检查。Windows 游戏中的识别和按键仍需实测。
