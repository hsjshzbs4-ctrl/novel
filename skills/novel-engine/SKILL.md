---
name: novel-engine
description: 用 `novel` CLI 写中文长篇网文——全书骨架、弧预演、章纲、批量写正文、一致性校验、成本记账都在引擎里。当用户要写/续写长篇小说、批量出章、检查伏笔或设定一致性、估算写作成本时使用。
---

# novel-engine

一个**自带状态的写作引擎**：Canon（人物/规则/伏笔/信息差）存在 SQLite 里，每章读相关的一小片、写完把变化写回。模型由引擎自己调用，你负责操作它。

> 和「提示词型技能」的区别：正文不是你来写，是 `novel write` 调模型写。你的工作是选范围、看结果、处理失败。

## 前提

- **Windows** + Python 3.10+，`novel` 命令可用（`novel --help` 能跑）
- DeepSeek API Key，放在作品目录的 `.env` 里
- 引擎默认模型 `deepseek-flash`，可用 `NOVEL_MODEL_*` 覆盖

## 第一次用

1. 确认命令存在：`novel --help`。没有就让用户双击 `scripts\install.cmd`，或跑 `powershell -ExecutionPolicy Bypass -File scripts\install.ps1`。
2. **不要**让用户在对话里粘贴 API Key。让他们写进 `.env`：
   ```
   NOVEL_PROVIDER=deepseek
   DEEPSEEK_API_KEY=sk-...
   ```
3. `novel doctor` 自检，确认 provider 和 key 状态。

## 命令地图

> 宿主支持 MCP 时，优先走 `novel mcp` 暴露的 10 个工具（同名逻辑，返回 JSON），比敲命令更省事。

| 命令 | 作用 | 何时用 |
|---|---|---|
| `novel init "书名" --genre 玄幻 --chapters 120 --words 3000` | 建项目 | 第一次 |
| `novel architect --idea "一句话创意"` | 角色/规则/信息差/弧 | 开书 |
| `novel rehearse --arc 1` | 整弧事件骨架 | 开写每一弧前 |
| `novel plan --count 10` | 章纲 | 批量写之前 |
| `novel write --ch 5` | 写一章 | 单章 |
| `novel batch --from 1 --to 20` | 连续写 | 批量 |
| `novel export --format docx` | 导出成册 | 交付给用户 |
| `novel status` | 进度 | 每轮结束 |
| `novel cost` | token/耗时账本 | 对账 |
| `novel recall "苏晚"` | 检索旧章节 | 查“这人上次出现” |
| `novel doctor` | 环境自检 | 出问题时 |

## 一次典型会话

```
用户：帮我开一本玄幻，写前十章
你：
1. novel init "断碑" --genre 玄幻 --chapters 120 --words 3000 --path <目录>
2. novel architect --idea "<用户给的一句话>" --path <目录>
3. 让用户确认角色和世界规则（读 outline/book.md 摘要给他看）
4. novel rehearse --arc 1 --path <目录>
5. novel plan --count 10 --path <目录>
6. 先写 1 章给用户看：novel write --ch 1 --path <目录>
7. 用户认可后再批量：novel batch --from 2 --to 10 --path <目录>
```

**不要**跳过第 6 步直接批量十章——先让用户看一章的语气和节奏，比重写十章便宜。

## 必须遵守

- **批量前先确认范围与花费**。`novel batch` 每章约 100 秒、几千 token；跑 50 章前先告诉用户预估时间。
- **不要把正文贴回对话**。让用户自己开 `chapters/ch-*.md`。
- **失败先看原因**：`ERROR` 类是硬伤（会标 rejected），`WARN` 类只是提醒；报告前先区分。
- **改设定后要重排章纲**：改角色卡/规则后，已生成的章纲不会自动更新，需重跑 `novel plan`。
- **模型名以 `novel doctor` 为准**，不要凭印象写。

## 常见故障

| 现象 | 处理 |
|---|---|
| `缺少 DEEPSEEK_API_KEY` | 让用户填 `.env` |
| `第 N 章正文为空或过短` | 模型抽风，重跑同一章；连续失败换 `NOVEL_MODEL_WRITER` |
| `评审失败，已跳过` | 评审被截断或返回非 JSON，不影响成稿；可 `--no-review` |
| `novel: command not found` | 未安装或未进 PATH，跑安装脚本 |
| pip 卡住 | 临时目录不可用，设置 `TEMP`/`TMP` 到可写目录 |
