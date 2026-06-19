# HappyClaude Client

TypeScript + Cloudflare Worker 客户端，用手机浏览器接入 `HappyClaude/Main` 里的 Python agent。

它参考 `one-main` 的本机代理思路：本机主动连接 Cloudflare Worker，手机只访问 Worker，电脑不需要暴露公网端口。

```text
手机浏览器 --https/wss--> Cloudflare Worker(Durable Object 中继)
                         ^
                         |
本机 client(TypeScript Node) --stdio/jsonl--> Python bridge --import--> MainLoop.agent_loop
```

## 1. 部署 Cloudflare Worker

```powershell
cd C:\Users\24021\Desktop\java\learnclaudecode\HappyClaude\client
npm install
npm run build
cd worker
npm install
Copy-Item wrangler.example.jsonc wrangler.jsonc
```

编辑 `client/worker/wrangler.jsonc`：

- `name`：Worker 名称，比如 `happyclaude-client`
- 如果你的 Wrangler 需要 `account_id`，按 Cloudflare 控制台填
- 如需自定义域名，再加 `routes`

部署：

```powershell
npm run deploy
```

部署完成后拿到 Worker 地址，例如：

```text
https://happyclaude-client.<你的子域>.workers.dev
```

## 2. 启动本机客户端

回到 `client` 目录：

```powershell
cd C:\Users\24021\Desktop\java\learnclaudecode\HappyClaude\client
$env:HAPPYCLAUDE_WORKER_URL="https://happyclaude-client.<你的子域>.workers.dev"
$env:HAPPYCLAUDE_SESSION_ID="换成一个长随机字符串"
npm start
```

控制台会输出：

```text
HappyClaude Cloudflare relay is ready:
  https://happyclaude-client.<你的子域>.workers.dev/?session=你的session
```

手机打开这个地址，就能远程连到本机 HappyClaude。

## 本地直连模式

不设置 `HAPPYCLAUDE_WORKER_URL` 时，仍然可用局域网直连：

```powershell
cd C:\Users\24021\Desktop\java\learnclaudecode\HappyClaude\client
npm start
```

打开控制台打印的 `http://你的局域网 IP:9527`。

## Python 环境

默认使用你的 conda 环境 `Claude`：

```text
C:\Users\24021\anaconda3\envs\Claude\python.exe
```

也兼容小写 `claude` 路径。若你要临时指定别的 Python，可以覆盖：

```powershell
$env:HAPPYCLAUDE_PYTHON="C:\path\to\python.exe"
npm start
```

端口可用：

```powershell
$env:HAPPYCLAUDE_CLIENT_PORT="9530"
npm start
```

远程 session 可用：

```powershell
$env:HAPPYCLAUDE_SESSION_ID="my-fixed-session"
npm start
```

## 当前能力

- 手机网页发送 prompt 给本机 HappyClaude
- 保留同一个 Python bridge 进程内的 `history`
- 展示 agent 日志、工具调用打印、最终 assistant 文本
- `write_file`、`edit_file`、风险 `bash` 等权限确认会弹到网页上
- 不修改 `Main/` 下现有 agent 代码
- Cloudflare Worker 只做 WebSocket 中继和静态资源托管，不保存 agent 数据

## 后续接近 One 的扩展点

- 加访问密码和 session cookie
- 拆分多个面板：Chat、Tasks、Files、Status
- 把 `.tasks`、`.memory`、`.mailboxes` 做成可视化页面
- 给 bridge 增加中断当前 run 的能力
