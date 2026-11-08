# 架构与上游差异

## 上游审查

上游 `.claude-plugin/` 注册插件和市场，`hooks/hooks.json` 加载 `hooks/register.ts`。TypeScript 模块通过 Claude 插件引擎订阅 `session.start`、`turn.start`、`tool.call`、`turn.complete` 和自定义命令。它按 Claude 工具名分类，用 turn 完成原因区分结束、错误和中断，并从 Claude 专用的会话用量 API 与存储读取统计。主代理状态写到 `~/.claude/desk-pet/sessions/<session-id>.json`，字段为 `state`、`text`、`project`、`ts`、可选 `sound`。

`pet/launch.py` 用 pythonw 启动分离的 Tk 进程。原 `pet.pyw` 绑定本地端口防止重复启动，每 250 毫秒扫描会话文件，按最新文件 mtime 显示状态并清除两天前的文件。Pillow 缩放立绘并处理透明色键边缘，Tk Canvas 负责气泡、抖动、呼吸和跳跃，winsound 异步播放 WAV。窗口唤醒从 EnumWindows 中选择首个 claude.exe 可见窗口，自动隐藏查询前台窗口的 PID。另有 tasklist 轮询检测 Claude 退出，坐标范围以主显示器为准。

| 处理 | 上游部分 |
| --- | --- |
| 直接保留 | MIT 许可证与版权、全部 PNG 和 WAV、原状态与立绘映射 |
| 延续并调整 | Tk Canvas、透明色键、动画公式、菜单交互、异步提示音、分离式启动 |
| 新实现 | Codex Hook 适配、并发写入、状态归约、选择会话、Win32 窗口识别和多显示器、安装卸载 |
| 移除 | Claude 插件清单、引擎注册脚本、Claude 用量 API、按 claude.exe 扫描和退出逻辑 |

## 数据流

```text
Codex command hooks
  -> hooks/codex_event.py
  -> hooks/codex_adapter.py
  -> pet/session_manager.py + pet/state_manager.py
  -> 独立会话 JSON
  -> pet/pet.pyw + pet/windows.py
```

Hook 读取一个 stdin JSON，仅返回空对象作为无动作响应。归约器保存稳定的会话 ID、项目目录、项目名、状态、活动轮次、事件时间、序号、近期去重键和可选用量。Codex 通用 Hook 字段未提供客户端归属，`client` 记录为 `unknown`。提示气泡显示的文件标签取 basename 并截短。状态不保存 Prompt、原始工具参数、命令输出、助手答复或 transcript 内容。项目目录保存在本机状态文件中用于会话识别和项目汇总。

同一会话的读取、去重和更新由操作系统文件锁串行化，并使用同目录临时文件及原子替换发布 JSON。会话文件名为 ID 的 SHA-256。无事件原始时间可用时以 Hook 进程接收时间排序，旧时间、已结束轮次和重复工具调用会被丢弃。同步 Hooks 缩小接收顺序与 Codex 发出顺序的偏差，超时时间限制为 3 秒，GUI 与 Hook 独立运行。

## 状态规则

- `SessionStart` 进入等待。提交任务、模型请求进入思考。压缩引发的 `SessionStart` 保持已有任务状态。
- `PreToolUse` 分类为阅读或执行。未知工具使用通用表情。`PostToolUse` 恢复剩余活动工具或进入思考，明确失败进入错误，连续三次错误触发石化。
- `PermissionRequest` 显示授权等待。桌宠不提供批准或拒绝。
- `Stop` 进入短暂庆祝后转等待。`Interrupt` 进入中断，`SessionEnd` 标为离线。等待十分钟后可显示发呆。活动轮次不因长时间静默转为完成。
- 自动选择最近活动的在线会话。固定会话可查看其离线状态。会话过期后清除。GUI 启动首轮只记录已有通知 ID，之后对选中的新通知播放一次。
- 视图每 300 毫秒检查会话文件，只重新解析已变化文件，按缩放后的像素尺寸缓存立绘。窗口失焦只做前台进程身份检查，显式唤醒才枚举窗口。

App Server 提供任务生命周期、工具项、审批和 Token 等 JSON-RPC 通知，适合自行管理连接的客户端。桌宠使用 Hook 观察现有 Codex，避免另启或接管用户的会话。独立 App Server 仅用于隔离测试。 [OpenAI Hooks 文档](https://developers.openai.com/codex/hooks) · [OpenAI App Server 文档](https://developers.openai.com/codex/app-server)
