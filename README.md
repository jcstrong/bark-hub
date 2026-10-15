# bark-hub

自托管的 Bark 推送管理与扇出网关：**一个事项（item）→ 多个 Bark 端点**，统一免打扰、推送历史、Web 管理台与 Bark 兼容接口。

> Self-hosted Bark notification hub: fan one item out to multiple Bark endpoints, with quiet hours, delivery history, a web console and a Bark-compatible API.

## 为什么造这个轮子

调研过现成方案（[message-pusher](https://github.com/songquanpeng/message-pusher)、[heimdallr](https://github.com/LeslieLeung/heimdallr) 等），它们分别偏向"多用户推送平台"和"Serverless 网关"，都没有同时满足：

- ✅ **事项 → 多端点扇出**：同一类通知可同时推到多个 Bark 服务器/设备，逐端点记录投递成败
- ✅ **统一免打扰窗口**：夜间自动降级为静默（passive），`force` 与 critical 类事项可绕过
- ✅ **Bark 兼容接口**：任何支持 Bark 的脚本把 URL 从 `https://api.day.app/<key>` 换成 `http://<hub>/<item_key>` 即接入，零改造
- ✅ **远程注册 API**：其他机器上的监控 agent 用一个 token 就能增改事项和端点
- ✅ **轻量单用户**：FastAPI + SQLite + 单容器，无用户系统

## 架构

```
                    ┌────────────────────────────────────────────┐
 任意 Bark 客户端    │                bark-hub                    │
 (老脚本只换 URL) ──▶│  Bark 兼容路由   POST /<item_key>           │      ┌─▶ https://api.day.app/<key1>  (iPhone)
                    │                 GET  /<key>/<title>/<body> │ 扇出 ├─▶ http://bark.lan/<key2>       (iPad)
 监控 agent ───────▶│  管理/推送 API   POST /api/items  (token)   │      └─▶ https://day.app/<key3>       (备份)
 (curl + token)     │                 POST /api/notify/<key>     │
                    │  SQLite: items / endpoints / notifications │
 人 ───────────────▶│  Web 控制台      免打扰 · 历史 · 事项 · 设置 │
 (浏览器)            └────────────────────────────────────────────┘
```

推送链路：请求进入 → 按 `item_key` 找到事项 → 免打扰/去重判定 → 扇出到该事项全部启用端点 → 逐端点写入投递记录（HTTP 状态、耗时、响应）。

## 快速开始

```bash
cp .env.example .env
printf 'BARKHUB_TOKEN=%s\n' "$(openssl rand -hex 24)" >> .env   # 或手填
docker compose up -d --build
```

打开 `http://<host>:9193/`，在「事项」页新建一个事项并添加 Bark 端点（server URL + 设备 key），点「测试」收到推送即接入成功。

## 接入

### 1. Bark 兼容推送（无需 token，item key 即凭证）

```bash
curl -X POST http://192.168.31.161:9193/bambu \
    --data-urlencode "title=打印完成" \
    --data-urlencode "body=P1S 已完成" \
    --data-urlencode "group=Bambu Lab" \
    --data-urlencode "level=active"
```

也支持 Bark 路径风格：`GET|POST /<item_key>/<title>/<body>?sound=minuet`。

### 2. 管理/注册 API（`X-API-Token` 头）

```bash
TOKEN=xxx; HUB=http://192.168.31.161:9193

# 注册/更新事项（upsert，endpoints 非空时全量替换）
curl -X POST $HUB/api/items -H "X-API-Token: $TOKEN" -H 'Content-Type: application/json' -d '{
  "key": "mac_backup", "name": "Mac 备份监控", "description": "189 TimeMachine",
  "endpoints": [
    {"name": "iPhone", "server_url": "https://api.day.app", "device_key": "yourDeviceKey"}
  ]
}'

# 推送（token 方式，适合已持有 token 的脚本）
curl -X POST $HUB/api/notify/mac_backup -H "X-API-Token: $TOKEN" \
    -H 'Content-Type: application/json' -d '{"title": "备份完成", "body": "增量 2.1GB"}'
```

其余：`GET /api/items`、`DELETE /api/items/{key}`、`POST/DELETE /api/items/{key}/endpoints`、`GET /api/notifications?item_key=&limit=&offset=`、`GET/PUT /api/settings`、`GET /api/status`（免鉴权）。交互式文档见 `/api/docs`。

### 3. 迁移旧 `bark-send` 脚本

仓库里的 [`scripts/bark-send`](scripts/bark-send) 是 drop-in 替代：CLI 兼容 v1（`bark-send 标题 内容 [group]`，`BARK_LEVEL`/`BARK_FORCE` 照旧），按 group 自动映射到事项 key，免打扰与历史全部由 hub 统一处理。设备 key 不再散落在各机器的脚本里。

## 配置

| 环境变量 | 说明 | 默认 |
|---|---|---|
| `BARKHUB_TOKEN` | 管理接口令牌（必填） | — |
| `BARKHUB_DB` | SQLite 路径 | `/data/bark-hub.db`（容器内） |

运行时设置（Web 或 `PUT /api/settings`）：免打扰窗口（支持跨午夜）、历史保留天数（默认 90，自动清理）、去重窗口（默认 60 秒，同事项同标题同内容只发一次）。

## 从 bark-hub v1（单文件面板）迁移

```bash
docker compose exec bark-hub python scripts/import_legacy.py \
    --db /data/bark-hub.db --legacy-dir /legacy \
    --server-url https://api.day.app --device-key <YOUR_KEY>
```

导入 v1 免打扰窗口与全部 JSONL 推送历史（按 group 映射到种子事项）。

## 开发

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
ruff check . && pytest -q
```

CI：GitHub Actions（ruff + pytest）。

## License

[MIT](LICENSE)
