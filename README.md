# Codex Desk Pet

Windows 透明桌宠。Codex 提交任务、执行命令、读文件、等待授权、结束或被打断时，桌宠根据本机 Hook 事件换表情。它可独立常驻，支持拖动、大小、音效、关注会话和 Codex 前台自动隐藏。

## 安装和启动

需要 Windows 10/11、带 Tk 的 Python 3.10+，以及支持相应生命周期 Hooks 的 Codex 客户端。安装器会检查 CLI 或桌面包，使用已有的 Pillow，或把依赖安装到桌宠目录。

在本仓库目录打开 PowerShell：

```powershell
.\install.ps1
```

Python 没有加入 PATH 时，传入解释器的实际路径：

```powershell
.\install.ps1 -PythonExe 'C:\Program Files\Python313\python.exe'
```

安装会启动桌宠并合并用户级 `hooks.json`。重启 Codex，在桌面端 **Settings → Hooks** 或输入框的 **Review hooks** 审查并信任桌宠条目。CLI 用 `/hooks`。打开新会话，提交一条只读任务。看到气泡变化，或运行目录里的 `sessions\*.json` 更新时间变化，即可确认 Hook 在运行。

关闭后可用 `.\start.ps1` 恢复。更新前从右键菜单退出桌宠，拉取或修改源码后重新运行安装脚本，并在 Codex 中审查变更过的 Hook。卸载前退出桌宠，再运行 `.\uninstall.ps1`。设置和本地会话记录默认保留。

## 桌面操作

| 操作 | 效果 |
| --- | --- |
| 拖动立绘或气泡 | 移动桌宠并保存位置 |
| 双击 | 唤醒可识别的 Codex Windows 桌面窗口 |
| 右键 | 设置小、中、大尺寸、提示音、前台隐藏和开机启动 |
| 关注会话 | 自动选择最近活动会话，也可固定一个会话 |
| 查看当前状态 | 显示当前状态、已观察的任务用时和可用 Token 数据 |
| 打开 Codex | 优先激活已有窗口，否则启动已识别的 MSIX 应用 |

开机启动由用户在菜单中启用，仅针对当前 Windows 用户。双击和自动隐藏识别的是桌面应用的窗口归属。CLI 和 VS Code 的窗口关联见兼容性说明。

## 自定义和排查

- 在 `pet/config.py` 改台词和立绘映射。替换 `pet/sprites/` 内同名透明 PNG 或 `sounds/` 内的 WAV 后重新安装。
- 默认运行目录为 `%LOCALAPPDATA%\CodexDeskPet`。其中 `settings.json` 保存界面设置，`sessions/` 保存本地会话状态。
- 详见[安装与配置](docs/installation.md)、[架构与上游差异](docs/architecture.md)、[故障排查](docs/troubleshooting.md)和[客户端兼容性与实测记录](docs/codex-compatibility.md)。

## 开发测试

```powershell
python -m unittest discover -s tests -v
python tests/windows_smoke.py
```

真实 Codex 二进制的隔离集成测试用法见[兼容性说明](docs/codex-compatibility.md)。

## 来源和许可

基于 [BingXing-ai/claude-desk-pet](https://github.com/BingXing-ai/claude-desk-pet) 的 `0784c254f9dead6d000dc5717a36ce19992f8c8d`，保留上游 Git 历史、原作者版权、立绘和音效。使用 [MIT 许可证](LICENSE)。本项目由社区维护。
