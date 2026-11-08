# 安装与配置

## Windows 当前用户安装

`install.ps1` 支持 Windows PowerShell 5.1 和 PowerShell 7。使用启用了 Tk 的 Python 3.10 或更新版本。必要时用 `-PythonExe` 传绝对路径。安装前可用 `python -m tkinter` 检查 Tk。如果没有可用的 Pillow，安装器使用 pip 将依赖安装到桌宠版本目录。

- `-CodexHome` 可指定独立的 Codex 配置目录。默认取 `CODEX_HOME`，否则使用 `%USERPROFILE%\.codex`。确认该目录与目标客户端一致。
- `-DataDir` 可指定独立的运行目录。用相同参数执行 `start.ps1` 和 `uninstall.ps1`。
- `-NoLaunch` 只安装和注册。适合隔离验证或后台部署。

脚本不需要管理员权限。注册时仅合并 `hooks.json` 的独立 matcher 组，保留其余组和顶层字段。配置不是合法 JSON 时会停止。安装目录按内容版本区分，更新后需要重启 Codex 和桌宠。旧版本会保留到卸载，以便已经启动的 Codex 进程完成它持有的 Hook 命令。

## Hook 信任审查

安装器生成的命令调用 Windows PowerShell，再执行本地 Python。采用 UTF-16LE 编码的命令参数是为兼容 cmd/PowerShell 的引号规则及中文、空格和单引号路径。审查时可以从桌宠条目末尾复制 Base64 参数，**只解码查看**：

```powershell
[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('<复制的参数>'))
```

解码后是调用已安装的 Python、`hooks/codex_event.py` 与桌宠运行目录的单条命令。核对后在桌面 **Settings → Hooks** 或 CLI 的 `/hooks` 中信任。安装器不写入信任记录。

## 数据和卸载

| 路径（在运行目录下） | 内容 |
| --- | --- |
| `releases/` | 已安装的应用版本、资源及本地依赖 |
| `installation.json` | 桌宠安装清单与注册项 |
| `settings.json` | 位置、尺寸、音效、前台隐藏、关注会话 |
| `sessions/` | 当前及近期会话的派生状态。默认保留最近两天 |
| `pet.log`、`pet-launch.log`、`launch.log` | GUI 与启动诊断 |
| `hook-error.log` | Hook 写入或解析失败的事件名与异常类型 |

先退出桌宠，再运行 `.\uninstall.ps1`。脚本按安装清单撤销自己的 Hook 和已启用的开机启动项，并移除所安装的版本。如果桌宠注册项已经手动编辑，先检查并移除该项，再重试。其他 Codex 配置、会话和设置保留。开机启动使用当前用户的 Windows Run 项，默认关闭。

如果已卸载但某个 Codex 窗口仍尝试运行旧 Hook，重启对应客户端，让它重新加载配置。
