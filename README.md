# OPC 一人公司 AI 操作系统（本地控制台）

一个双击就能启动的本地 AI 操作台：**能调本地模型、能调云端大模型、能一键接入 GitHub 上的 MCP 工具**，并把参赛材料（计划书）一键生成。
只依赖 Python 标准库 + 本机已有的 Ollama / Node，无需装任何框架。

## 启动

双击桌面上的 **OPC一人公司.cmd**（或本目录 `启动OPC.cmd`），浏览器会自动打开 `http://127.0.0.1:8770/`。
停止服务：运行 `停止服务.cmd`。

## 三个页面

| 页面 | 作用 |
|---|---|
| **对话台** | 选择模型（自动路由 / 本地 / 云端任一条腿）直接对话。自动模式：有本地对话模型先走本地，失败无感切云端 |
| **MCP 接入** | 从目录点「接入」写进配置，点「体检」真实握手并列出该服务器的工具 |
| **参赛任务** | 报名信息字段（已按你的真实资料预填）→ 保存 → 生成/下载 Markdown 计划书 |

## 模型路由

- **本地**：Ollama 实例 `127.0.0.1:11435`（模型目录 `D:\ai\ollama-models`）→ 回落 `11434`
- **云端**：aihubmix（deepseek-v4.1-flash / gpt-6.2-sol / glm-5.3-flash / kimi-k3.1-pro）、DeepSeek 官方、OpenRouter
- key 从 `C:\Users\czy\AppData\Local\hermes\.env` 读取，不写进代码

## 已实测（2026-11-24）

| 项 | 结果 |
|---|---|
| 界面 + API | 200，UI 正常渲染 |
| 云端对话 | `deepseek-v4.1-flash` 1.9 秒返回真实回答 |
| MCP 接入 | `@modelcontextprotocol/server-filesystem` 握手成功，返回 16 个工具 |
| MCP 工具调用 | `list_allowed_directories` / `list_directory` 真实读到桌面文件列表 |
| 计划书生成 | 8 个章节完整输出 |
| 本地模型拉取 | **原目录 `D:\ai\.ollama` 被火绒 HIPS 拦写（Access denied）**；改用 `D:\ai\ollama-models` + 独立端口 11435 后拉取成功 |

## 已知问题与处理

- **火绒拦截本地模型下载**：报 `Access is denied`。解决：模型目录改到 `D:\ai\ollama-models`，由启动脚本自动拉起 11435 实例。
- 拉取大模型期间，11435 会忙于下载、`/api/tags` 可能超时，界面会暂时显示系统 Ollama（11434）的模型；下载完成后自动正常。
- 16GB 内存：同一时刻只跑一个本地模型，出片/3D 任务前先关掉本地 LLM。

## 目录

```
D:\ai\opc-os\
  server.py         后端（模型路由 / MCP 客户端 / 任务 / 计划书）
  ui.html           单页界面
  启动OPC.cmd       一键启动
  停止服务.cmd      停止
  mcp_registry.json MCP 可选目录（含真实仓库与星数）
  mcp_servers.json  已接入的服务器
  task\task.json    参赛信息（可改）
  task\计划书_草稿.md 生成结果
```
