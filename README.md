# Claude Link

B站 项目效果演示：https://www.bilibili.com/video/BV1aiYUzZE9P/

通过此MCP，Claude Code 现在获得“元修改”能力：**Claude Code 可以调用它自己，并使用（再）启动出来的 Claude Code**。

每个 agent 都是一个独立 tmux pane 里运行的完整 Claude Code，拥有人类使用时的全部能力。

开箱提供最小但好用的接口：创建/关闭 agent、主动查看并操纵其他agent屏幕内容、收件箱/强制终端推送 到其他agent 文字/自己终端的截屏内容、工作完成后固定执行强制推送文字/截屏到指定agent。
（这里的其他也可以是agent自己本身）

## 这到底解决了什么问题？
- **CC 使用 CC 的服务**：不是把官方的繁多服务变为一组mcp，而是让 Claude Code 直接使用 Claude Code。因此功能会随官方一起演进、同步，无需维护分叉。
- **不中断的环境**：每个agent 是真实 Claude Code 进程，不是临时的“函数调用”。它们能长时间保持上下文、历史与工具状态。
- **可视化地查看与操作，随时干预任何Agent**：因为 subagent 就是完整的 Claude Code，你天然拥有 CC 的可视化界面，能够上下浏览与编辑配置、运行面板、历史上下文等。你可以随时在任意 pane 中直接输入与干预。
- **用尽你定义过的一切命令**：subagent 能使用并修改你在 CC 里能用的所有工具/命令（command），修改也不用通过直接改.claude而是通过调用claude code的服务实现的的。使用和修改这两项能力在claude code原生subagents都未提供。
- **平级创生（Peer‑create）**：每个 agent 都可以拥有同等的创造/控制权，可彼此创建、相互协作，不必固化为单向父子；拓扑可以从树自然演进为网状。
- **双向流通知**：
  如果你决定让两个agent是父子关系，那么
  - 父→子：父代理随时实时查看子代理（抓取文本“截图”）、注入指令
  - 子→父：通过 Hook 在“子任务完成”时主动通知父代理
- **Agent 生 Agent**：代理可以修改代理的配置，尤其是可以创建claude code原生subagents，这在勉强用“只改 .claude 文件”的方案里会绕开 /agents 服务，失去诸多好处。


## 它是怎么做到的？
在 tmux 里，每个 Claude Code 会话就是一个“pane”。`claude_link` 暴露一组极简工具（MCP Tools）：创建新的 pane（= 新的 Claude Code）、往某个 pane 注入输入、抓取它的文本历史、给它发消息、关闭它。于是“Claude Code 用工具”→“工具再开启/控制 Claude Code”，形成闭环。

## 五分钟了解 Claude Link 提供的 MCP Tools
- whoami：返回当前 pane 的 `id`、`workdir`、`father?`。
- list：列出所有 tmux pane（包含路径、标题、父子关系）。
- start_new_session_and_get_return_id：创建一个新的 Claude Code subagent；可指定 `workdir` 与 `workdir_policy`（`require_empty_existing`/`use_existing`/`create_new`/`create_or_empty`）；可选 `add_hook`、`hook_mode`（`text`/`screenshot`）、`calledagent`、`text`。
- get_screenshot_from：抓取目标 pane 的“文本截图”（完整文本历史）。
- send_message_to：被动消息；写入对方收件箱（需对方调用 `check_message_box` 拉取）。
- inject_text：主动强送达（文本注入）；原样粘贴到目标输入框，可选 `with_from`、`submit`、`mode`（`append`/`replace`）。
- inject_keys：主动强送达（按键注入）；透传 tmux 键名（如 `Down`、`Enter`、`C-m`、`S-Tab`）。
- check_message_box：拉取自 `since_id` 以来的收件箱消息，返回 `messages` 与新的 `since_id`。
- add_callback_hook_when_completed：在项目配置里追加 Stop Hook（子任务完成时通知另一个代理；支持 `text` 或 `screenshot` 模式）。
- kill_pane_and_agent：关闭目标 pane（终止该 subagent）。

## 为什么说“是真正的 agents”？
- **不是“轻量助手”**：很多“子代理”只是一次性函数或沙盒执行。这里的 subagent 是独立进程中的完整 Claude Code。
- **保留全部能力**：它们能使用同样的权限、工具、项目上下文与交互界面（因为就是你平时用的 CC）。
- **可长时运行**：tmux pane 持久存在，长期任务/多轮对话/历史都能保留。

（以上均可在 Claude Code 的 MCP 工具面板中直接使用；无需命令行。）

## 安全默认与可配置项
- **启动命令**：`CLAUDE_CMD` 控制新 pane 内启动什么（默认 `claude --dangerously-skip-permissions`，按需替换你的 CC 启动方式）。
- **运行目录**：`CLAUDE_LINK_ROOT` 或 `XDG_RUNTIME_DIR` 下的 `claude-link`。

## 安装与使用
- 安装（推荐开发模式）：

  确保你安装了python>= 3.9 和 tmux 和 claude code。

  在仓库目录下
  ```bash
  pip install -e .
  ```
- （可选以验证跑通）

  测试运行服务端（MCP）：
  ```bash
  claude-link
  ```
  什么都不会发生
  
  然后在另一个终端测试客户端：
  ```bash
  claude-link-call --server "claude-link" --method tools/list --output result
  ```
- 在项目中启用
  - 将 `claude_link/.mcp.json` 复制到项目根的 `.mcp.json`。
  - (推荐) 将 `claude_link/.claude/settings.local.json` 复制到项目根的 `.claude/settings.local.json`以自动允许该mcp调用。
```
  - 之后 tmux 中的 claude code 可直接发现并启动该 MCP 服务。**只有在tmux中的claude code里该mcp才能运行成功**

## FAQ（你可能会关心）
- 我需要改代码吗？不。本文档刻意不贴实现细节，你只要会调用这几个接口即可。
- 一定要 tmux 吗？是的，当前依赖 tmux pane 来承载多个 CC 会话。
- Windows 支持？暂不支持（依赖 `fcntl` 与 tmux）。
- 人类如何介入？随时切到对应 tmux pane 输入即可；这与平时使用 CC 没有任何区别。

## 一句话总结
**Claude Link = 让 Claude Code 能自我复制、彼此协作的最小系统。**

## 畅想场景（网状·群聊·可自举·可克隆）
- **网状协作**：多对多代理联接，形成能力路由图；支持广播/订阅、（todo）基于角色/能力的寻径与调度。
- **群聊模式**：多代理之间形成工作区，（todo）群聊与好友模式。
- **可自举**：系统用自身完成自身系统的升级，自托管运行，最小人类干预。
- **可克隆**：agent具备了病毒式传播的能力，它可以**复制自身**


## 使用方式与能力对比（扩展）

| 维度 | claude-link | ht-mcp | claude-code-mcp | git worktree + tmux + main agent + task list调度 | claude code 原生 subagents |
|---|---|---|---|---|---|
| 多工作目录 | ✅ | ✅ | ❌ | ✅ | ❌ |
| 持久化 | ✅ | ✅❌| ❌ | ✅ | ❌ |
| 可实现多层级 agents 架构 | ✅ | ✅ | ❌ | ❌ | ❌ |
| 创生/销毁其他 agents | ✅ | ✅ | ❌ | ✅ | ❌ |
| 可实现网状平级沟通 | ✅ | ❌ | ❌ | ✅❌ | ❌ |
| 可视化 | ✅ | ✅ | ❌ | ✅ | ❌ |
| 人类可介入 | ✅ | ✅❌ | ✅ | ✅ | ❌ |
| 能调用 /commands | ✅ | ✅ | ❌ | ❌ | ❌ |
| 开箱即用 | ✅ | ❌ | ✅ | ✅❌ | ✅ |
| 支持父查询子 | ✅ | ✅ | ❌ | ✅ | ❌ |
| 支持子通知父 | ✅ | ❌ | ❌ | ❌ | ✅ |
| 能自我修改 | ✅ | ✅❌ | ❌ | ❌ | ✅❌ |