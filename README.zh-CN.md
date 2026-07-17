# AniyaAgent

[English](README.md) | [简体中文](README.zh-CN.md)

一个跑在自己电脑上的私人 AI 助手。它可以聊天、记住重要信息、管理待办和日程，并把提醒发到微信。数据和工具都留在你的设备侧，助手负责把这些能力串起来。

## 能做什么

- **陪你做事**：理解自然语言，调用本地工具，完成文件、命令、任务等操作。
- **记得住事**：区分事实记忆、每日记忆和长期记忆，让上下文不会越聊越乱。
- **管好生活**：待办、例行事项、定时提醒和后台任务各归其位。
- **随时能访问**：桌面或手机浏览器均可使用；需要时可通过 Cloudflare Worker 中转远程访问。
- **提醒不打扰**：微信通道只用于通知，不是聊天入口。

## 架构

![AniyaAgent 架构图](AnyaArchitecture.png)

一次请求会经过以下路径：

1. **入口层**：桌面或手机浏览器进入 Web Client；远程场景可经 Worker 安全中转。
2. **访问层**：Owner Token 保护私有访问，避免把本机助手暴露成公开服务。
3. **运行时**：Agent Runtime 负责理解请求、调用 LLM、编排工具，并把结果带回对话。
4. **状态层**：三层记忆保存事实、当天脉络和确认后的长期信息；任务、例行和提醒由调度器持续处理。
5. **通知层**：需要你知道时，运行时通过微信发送提醒；微信不会接管对话。

## 快速开始

环境要求：Python 3.10+，以及一个兼容 Anthropic 或 OpenAI API 的模型服务。

```powershell
cd C:\Users\24021\Desktop\java\learnclaudecode\AniyaAgent
pip install -r main/requirements.txt
Copy-Item main/.env.example main/.env
```

编辑 `main/.env`，至少配置 `ANIYAAGENT_OWNER_TOKEN` 和所选模型服务的 API Key / Model ID。

启动网页客户端（它会自动启动本机 Agent 服务）：

```powershell
cd main/client
npm install
npm run build
npm start
```

终端会显示桌面和局域网地址。用手机打开局域网地址，输入 Owner Token 后即可使用。

需要定时提醒、例行事项和微信通知时，另开一个终端启动调度器：

```powershell
python -m main.channel.run_scheduler
```

也可以不启动网页，直接在终端运行 Agent：

```powershell
python -m main.agent.main_loop
```

## 手机访问

本地使用时，打开网页客户端输出的局域网地址即可。远程访问可部署 `main/client/worker` 中的 Cloudflare Worker，并为 `ANIYAAGENT_WORKER_URL` 与 `ANIYAAGENT_SESSION_ID` 配置独立、足够随机的值。

## 安全提示

`main/.env` 中含有模型密钥和访问令牌，绝不能提交到仓库。AniyaAgent 面向个人私有使用；若要提供给多人使用，需要自行补齐认证、授权、审计和更严格的执行隔离。

## License

暂未指定许可证。
