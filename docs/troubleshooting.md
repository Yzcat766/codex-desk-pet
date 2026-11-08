# 故障排查

## 窗口没有出现

1. 用 `.\start.ps1` 手动启动。使用过 `-DataDir` 时传入相同目录。
2. 打开运行目录的 `pet-launch.log`、`launch.log` 和 `pet.log`。如果缺少 Tk，换用安装了 Tcl/Tk 的 Python。缺少 Pillow 时重新运行 `.\install.ps1`。
3. 位置信息保存在 `settings.json`。退出桌宠后可移除其中的 `x` 和 `y`，下次启动时使用默认位置。

## 桌宠显示离线或状态不更新

1. 在 Codex 中确认桌宠 Hook 出现在审查界面中，且已信任并启用。编辑或升级 Hook 后需要重新审查。
2. 用新会话执行一个短命令，例如要求 Codex 使用 `Get-Content README.md -TotalCount 1`。查看运行目录下 `sessions/` 的修改时间。
3. 如果 Hook 报错，检查 `hook-error.log` 和 Codex 的 Hook 运行记录。如果没有任何 Hook 记录，检查使用的 `CODEX_HOME`、客户端版本以及管理员是否关闭了用户级 Hooks。
4. 桌面客户端如果运行在 WSL 内，Linux Hook 与本项目的 Windows Python 注册命令不兼容。兼容性表以 Windows 原生执行环境为准。

## 声音、窗口唤醒和会话

- 右键检查提示音开关和关注会话。桌宠启动前的通知不会重播。检查安装目录的 WAV 和 Windows 音量设置。
- 双击依赖 Windows 前台切换权限。可查看 `pet.log` 中的 `active`、`denied`、`ambiguous` 或 `missing`。存在多个匹配窗口时，先切到希望唤醒的 Codex 窗口一次。旧版或未来变更的 MSIX 身份需要重新验证。
- 如果某个进程异常退出，可能收不到 `SessionEnd`。该状态会保留到有新事件或会话过期；右键可以改为关注其他会话。
- 如果卸载报告运行中，先用右键菜单退出桌宠。若 `hooks.json` 解析失败，先用编辑器修复语法后重试。

[OpenAI Hooks 文档](https://developers.openai.com/codex/hooks)
