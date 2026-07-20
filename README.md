# A股日复盘

每日收盘后自动生成标准版复盘页（Web/H5），并通过微信订阅号群发链接推送。

## 功能

- 大盘指数（上证 / 深成 / 创业板）
- 涨跌家数
- 北向资金
- 自选股当日表现
- 板块涨跌榜
- 成交额 / 换手热点
- 规则生成的简要文字点评
- 交易日 15:35（北京时间）自动生成 + 微信群发

## 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入微信 AppID / AppSecret、站点地址、自选股等
```

### 2. Docker 部署

```bash
docker compose up -d --build
```

服务默认监听 `http://0.0.0.0:8000`。

- 今日复盘：`http://你的域名/`
- 指定日期：`http://你的域名/review/2026-07-20`
- 手动触发生成：`POST /api/generate`（可选 `?push=true` 同时推送）
- 手动推送今日：`POST /api/push`

### 3. 本地开发（不经过 Docker）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 微信订阅号

1. 登录[微信公众平台](https://mp.weixin.qq.com/) → 开发 → 基本配置，拿到 `AppID`、`AppSecret`
2. 账号需为**认证**订阅号，才可调用高级群发接口（每天 1 次）
3. 群发内容为文字消息，包含当日复盘 H5 链接
4. 可先把 `WECHAT_PUSH_ENABLED=false`，确认页面无误后再开启推送

未认证或个人订阅号若无法调用群发接口，仍可打开复盘页手动复制链接，或在公众平台网页端手动群发。

## 目录结构

```
app/
  main.py           # FastAPI 入口 + 定时任务
  config.py         # 配置
  data_fetcher.py   # akshare 行情拉取
  report.py         # 复盘快照生成与点评
  wechat.py         # 订阅号 access_token + 群发
  templates/        # H5/Web 页面
  static/           # 样式
data/               # 每日 JSON 快照（挂载卷）
```

## 部署检查清单

1. 服务器安装 Docker / Docker Compose
2. 复制项目，填写 `.env`（`BASE_URL` 必须是公网可访问地址，微信内打开 H5 用）
3. `docker compose up -d --build`
4. 浏览器访问 `/review/2026-07-18` 看示例页；交易日收盘后访问 `/` 或调 `POST /api/generate`
5. 确认微信群发可用后设 `WECHAT_PUSH_ENABLED=true`，再 `POST /api/push` 试一次（注意每天限额 1 次）

反向代理示例（Nginx）：

```nginx
server {
    listen 80;
    server_name your-domain.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```
