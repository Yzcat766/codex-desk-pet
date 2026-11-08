# Codex 兼容性与实测

实测环境为 Windows 11 build 26300、Python 3.13.13、Tk 8.6.15、Pillow 12.2.0。Codex CLI 为 `0.167.0-alpha.3.2`，已安装的桌面包 `OpenAI.Codex` 为 `26.1103.4239.0`。不同渠道和版本可能有不同的事件覆盖。

| 客户端或路径 | Hooks | 窗口功能 |
| --- | --- | --- |
| Windows Codex CLI | **PASS** · 启动、提交、模型请求、读文件、工具完成、任务结束及关闭 | **UNSUPPORTED** · 通用 Hook 没有 Windows Terminal/控制台句柄 |
| 独立 Codex App Server | **PASS** · 配置发现、信任审查、中断、结束会话和模型请求失败 | **UNSUPPORTED** · 测试传输为 stdio |
| Windows Codex 桌面应用 | **UNTESTED** · 当前账户的桌面聊天与 Hook 未做端到端测试 | **PASS** · 枚举并识别本机真实 MSIX 主窗口。真实鼠标双击和最小化恢复 **UNTESTED** |
| VS Code Codex 扩展 | **UNTESTED** · 本机未运行扩展端到端测试 | **UNSUPPORTED** · 目前无可靠的聊天与 VS Code 窗口关联 |
| Windows 桌面端使用 WSL 执行 | **UNSUPPORTED** · 安装器注册 Windows 原生命令 | 桌面宿主识别与 Linux Hook 执行分开 |

本机真实桌面窗口归属为 MSIX `OpenAI.Codex_2p2nqsd0c76g0` 中的 `ChatGPT.exe`，应用 ID 为 `OpenAI.Codex_2p2nqsd0c76g0!App`。测试中后台 `codex.exe` 没有主窗口。窗口选择核对 HWND、PID、包身份、进程文件名和可见性。存在多个候选时采用最近观察到的前台窗口，仍有歧义时不选。后台进程直接请求切换前台的测试为 **FAIL**，Windows 拒绝了切换。实际鼠标路径需手动测试。

## 事件和字段

| Hook | 取用字段或条件 | 桌宠行为 |
| --- | --- | --- |
| 所有事件 | `hook_event_name`、`session_id`、`cwd`，轮次事件另有 `turn_id` | 区分会话、项目与轮次 |
| SessionStart / SessionEnd | `source`、`reason` | 等待 / 离线。压缩期间的重新启动保持现有状态 |
| UserPromptSubmit / PreModelRequest | `turn_id` | 收到任务 / 思考 |
| PostModelRequest | `status`、可用的 `token_usage` | 思考、明确失败和已观察用量 |
| PreToolUse / PostToolUse | `tool_name`、`tool_use_id`、局部检查 `tool_input` / `tool_response` | 区分阅读和工具工作。明确的结构化退出码或 MCP 失败标记可进入错误状态 |
| PermissionRequest | 有该事件即可 | 提示需要确认 |
| Stop / Interrupt | `turn_id` | 完成提示 / 被打断 |

官方列出的本地 Shell、补丁、MCP 和部分本地工具可触发工具 Hooks。托管 WebSearch 和某些专用路径没有通用工具 Hooks，桌宠不会推测它们正在联网。Bash 的分类基于保守的命令识别，复合命令或未知工具使用通用敲键盘表情。包含子代理标识的事件不覆盖主代理状态。第三方 Stop Hook 可以请求继续，桌宠只看到自身的 Stop 回调及之后的新事件。

本机统一终端工具的 `PostToolUse` 未提供可识别的退出码。实际命令退出 7 时，CLI 的模型可见输出报告了退出码，Hook 只能可靠地把状态从工具工作切回思考。该路径的失败表情为 **UNSUPPORTED**。命令输出正文不会被当作退出状态。 [Codex 统一终端工具 Hook 实现](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/context.rs)

Token 数据来自实际收到的主会话 `PostModelRequest` 计数。本项目汇总覆盖仍保存的同目录会话，默认最近两天。上下文使用百分比、历史完整账单、费用和账号额度当前 **UNSUPPORTED**。App Server 能通知连接客户端的线程用量与上下文窗口，本项目未订阅正在运行的桌面客户端连接。

## 验证命令

```powershell
python -m unittest discover -s tests -v
python tests/windows_smoke.py
python tests/codex_smoke.py --run-vetted-fixture
```

集成脚本创建新的隔离 Codex 配置和只含测试文件的项目。它先验证未经信任的配置不会触发，再通过 Codex 自身的配置接口核对命令来源和精确哈希，审查临时配置中的这组 Hooks。模型请求由本机回环夹具响应。这个环境能验证真实 Codex 事件分发，但与登录账户、远程模型和桌面聊天的实测相区分。

原生 GUI 检查验证 Win32 透明色键、无边框、置顶、拖动逻辑、位置保存、尺寸、气泡边界、菜单构造、WAV 调用、缺失资源回退、受控前台隐藏和独立进程锁。肉眼确认半透明边缘、实际声音、跨实体显示器 DPI、双击唤醒、最小化恢复和 Windows 重新登录后自动启动均需手动验证。

## 验收记录

| 验收项 | 结果 |
| --- | --- |
| 适配、状态转换、多会话、异常、锁和安装回滚 | **PASS** · 12 项自动化测试 |
| PowerShell 安装、手动启动、窗口退出和卸载 | **PASS** · 临时运行目录。另以包含中文和空格的解释器路径验证安装和 Hook 写入 |
| 原生 GUI 和真实 Codex 窗口枚举 | **PASS** · Windows 11 交互式会话 |
| Codex CLI 实际 Shell 读取及结束 | **PASS** · 观察到 `SessionStart → UserPromptSubmit → PreModelRequest → PreToolUse(reading) → PostToolUse(thinking) → Stop(celebrate) → SessionEnd(offline)`。多次模型调用可穿插在工具事件前后 |
| App Server 中断、归档和失败 | **PASS** · `turn/interrupt` 触发 `Interrupt`，`thread/archive` 触发 `SessionEnd`，夹具 HTTP 500 触发 `PostModelRequest` 失败并显示错误状态 |
| PermissionRequest、VS Code 和登录账户下的桌面聊天 | **UNTESTED** · 授权状态已通过适配与状态机测试 |
| Windows 10、实体多显示器、耳听提示音、登录后自启动 | **UNTESTED** |
| 托管 WebSearch 的工具级状态、EXE 打包 | **UNSUPPORTED** |

本次 App Server 测试中的同步 Hook 执行时间为 541–577 毫秒，包含 Windows 启动命令进程的开销。界面在独立进程，Hook 注册超时为三秒。

参考 [OpenAI Hooks](https://developers.openai.com/codex/hooks)、[OpenAI App Server](https://developers.openai.com/codex/app-server)、[Codex 源码](https://github.com/openai/codex)。
