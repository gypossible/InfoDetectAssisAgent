# 自动化舆情监测与报告生成 Agent

一个可直接扩展的 Python 项目，用于每天自动读取本地 Excel 监测名单，抓取过去一年的新闻舆情，生成原始数据表与 AI 深度分析报告，并通过邮件发送给指定收件人。

## 1. 项目结构

```text
.
├── .env.example
├── .streamlit
│   └── secrets.example.toml
├── main.py
├── streamlit_app.py
├── web_app.py
├── opinion_monitor
│   ├── config.py
│   ├── data_processing.py
│   ├── email_dispatcher.py
│   ├── excel_reader.py
│   ├── logging_utils.py
│   ├── models.py
│   ├── pipeline.py
│   ├── report_generator.py
│   ├── scheduler.py
│   └── search_clients.py
└── requirements.txt
```

## 2. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` 包含：

```text
openai
pandas
openpyxl
requests
schedule
python-dotenv
duckduckgo-search
tavily-python
Flask
gunicorn
streamlit
```

## 3. 配置说明

1. 复制配置模板：

```bash
cp .env.example .env
```

2. 修改 `.env` 中的关键参数：

- `EXCEL_INPUT_PATH`：本地辅助模式使用的 Excel 文件或目录路径；网页/Streamlit 默认仍以上传单个 Excel 为主
- `EXCEL_TARGET_COLUMN_LETTER`：默认 `B`，表示扫描各个 sheet 的 B 列
- `SEARCH_PROVIDER`：默认 `tavily,qcc,duckduckgo`，支持用英文逗号串联多个来源；也支持单独使用 `tavily`、`qcc`、`duckduckgo`、`bing`、`serpapi`
- `SEARCH_LOOKBACK_DAYS`：默认 `365`，表示搜索过去一年舆情
- `MAINLAND_SOURCE_MODE`：默认 `prefer`，表示优先中国大陆公开站点；可选 `off / prefer / only`
- `MAINLAND_SOURCE_DOMAINS`：中国大陆来源域名白名单，支持自行扩展
- `TAVILY_API_KEY`：Tavily 搜索 Key
- `QCC_APP_KEY` / `QCC_SECRET_KEY`：企查查开放平台新闻接口鉴权配置
- `QCC_ACCOUNT`：企查查开放平台账号（可选，仅作记录）
- `QCC_AUTO_DISABLE_ON_REGION_BLOCK`：默认 `true`。当企查查返回“数据不能出境”或“暂不支持境外 IP 请求”时，本轮任务自动停用企查查并继续使用其他来源
- `REQUEST_RETRY_ATTEMPTS` / `REQUEST_RETRY_BACKOFF_SECONDS`：搜索限流后的自动重试与退避配置
- `ANNOTATED_EXCEL_NEWS_LIMIT`：写回到名单 Excel 的单主体舆情条数，默认 `10`
- `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `LLM_MODEL`：大模型配置
- `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD`：发件邮箱 SMTP 配置
- `EMAIL_RECIPIENTS`：收件人，默认已设置为 `liuguangyuan@natrust.cn`

说明：

- 企查查官方新闻接口使用 `APPKEY + SecretKey + Token(MD5(APPKEY+Timespan+SecretKey))` 鉴权。
- 登录账号本身通常不能直接替代 `APPKEY`，需要到企查查开放平台“账号安全”页面获取真实 `APPKEY` 与 `SecretKey`。
- 如果部署环境被企查查判定为境外出口，接口可能返回 `121：数据不能出境` 或 MCP 返回 `100002：暂不支持境外IP请求`。默认配置会在本轮任务中自动跳过企查查，避免整批主体反复失败。

## 4. Excel 格式要求

系统默认以**上传的单个 Excel 工作簿**作为监测名单来源，并自动扫描：

- 每个工作簿中的所有 Sheet
- 每个 Sheet 的 `B` 列非空单元格

例如：

| A列 | B列 |
| --- | --- |
| 序号 | 某上市公司 |
| 1 | 某品牌 |
| 2 | 某机构 |

程序会自动全局去重，并自动跳过诸如 `主体名称`、`主体`、`发行人名称` 之类的常见表头值。
本地固定路径和 `--excel-path` 仍可作为辅助入口使用，但不再作为网页主流程强调目录整体扫描。

## 5. 运行方式

### 单次执行

```bash
python main.py
```

如需在命令行下使用本地辅助文件，可临时指定 Excel 文件或目录：

```bash
python main.py --excel-path "/Users/guangyuan/Documents/舆情监测主体/某个名单.xlsx"
```

### 常驻调度模式

```bash
python main.py --schedule
```

默认每天 `07:30` 执行一次，可通过 `SCHEDULE_TIME` 修改。

### 网页上传模式

```bash
python web_app.py
```

启动后访问 [http://127.0.0.1:7860](http://127.0.0.1:7860)，即可：

- 上传一个单独的 Excel 工作簿作为本次监测名单
- 系统自动读取所有 sheet 的 B 列主体名称
- 系统会自动跳过明显像债券简称/代码的条目，例如 `20弋阳01`
- 默认搜索链路支持串联 `Tavily News`、`企查查新闻`、`DuckDuckGo News`
- 使用 Tavily 时，如命中限流或结果为空，会自动用 DuckDuckGo News 兜底
- 网页会显示实时执行进度条，完成后可直接下载“写回舆情后的 Excel”
- 如确需复用本地文件，可在辅助模式中明确选择一个已有 Excel 工作簿

### 免费公开部署模式（Streamlit）

```bash
streamlit run streamlit_app.py
```

如果想免费公开访问，建议将仓库部署到 Streamlit Community Cloud。官方文档说明：

- Community Cloud 免费可用，并会生成公开的 `streamlit.app` 地址
- 部署时直接选择 GitHub 仓库、分支和入口文件即可

相关文档：

- [Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud)
- [Deploy your app](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
- [Connect your GitHub account](https://docs.streamlit.io/deploy/streamlit-community-cloud/get-started/connect-your-github-account)
- [Secrets management](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)

建议部署参数：

- Repository: `gypossible/InfoDetectAssisAgent`
- Branch: `main`
- Main file path: `streamlit_app.py`
- Secrets: 将 [.streamlit/secrets.example.toml](/Users/guangyuan/Documents/重点主题舆情监测定时任务/.streamlit/secrets.example.toml) 的内容复制到 Streamlit 的 Secrets 配置框中，再填入真实密钥
- 使用方式：默认上传一个 Excel 工作簿，系统自动扫描所有 sheet 的 B 列

## 6.1 公开部署

本项目已经附带 [render.yaml](/Users/guangyuan/Documents/重点主题舆情监测定时任务/render.yaml)，适合直接从 GitHub 部署到 Render。

部署要点：

- Web 入口使用 `gunicorn web_app:app`
- 健康检查路径为 `/healthz`
- 上传文件目录和默认 Excel 目录在线上统一指向 `data/uploads`
- `TAVILY_API_KEY`、`QCC_APP_KEY`、`QCC_SECRET_KEY`、`OPENAI_API_KEY`、SMTP 账号密码等敏感信息请在 Render 后台环境变量中填写，不要提交到仓库

## 6. 输出内容

程序执行后会在 `outputs/YYYYMMDD/` 目录下生成：

- `舆情原始数据_YYYYMMDD.xlsx`
- `每日舆情分析报告_YYYYMMDD.md`
- `舆情写回名单_YYYYMMDD.xlsx`

日志会写入 `logs/opinion_monitor.log`。

## 7. 定时任务建议

### Linux crontab

建议在 `06:50` 左右启动脚本，给抓取、分析和邮件发送预留缓冲时间：

```bash
50 6 * * * cd /你的项目目录 && /usr/bin/python3 main.py >> logs/cron.log 2>&1
```

### Windows 任务计划程序

可使用命令创建每日任务：

```powershell
schtasks /Create /SC DAILY /TN "PublicOpinionMonitor" /TR "python C:\你的项目目录\main.py" /ST 06:50
```

## 8. 模块说明

- `excel_reader.py`：读取本地 Excel 中的监测主体名单
- `search_clients.py`：封装 `Tavily`、`企查查新闻`、`DuckDuckGo`、`Bing News Search`、`SerpAPI` 搜索接口
- `data_processing.py`：清洗、去重、打标签并导出原始 Excel
- `report_generator.py`：调用 LLM 生成不少于 1500 字的 Markdown 舆情分析报告
- `email_dispatcher.py`：通过 SMTP 发送正文和附件
- `scheduler.py`：提供 `schedule` 常驻调度入口
- `pipeline.py`：串联完整业务流程
- `web_app.py`：提供本地网页上传和触发执行入口
- `streamlit_app.py`：提供免费公开部署用的 Streamlit 入口

## 9. 使用提醒

- 默认搜索链路是：`Tavily News` + `企查查新闻` + `DuckDuckGo News`；也支持手动切换为 `Bing News Search API` 和 `SerpAPI (Google News)`。
- 当前已增加中国大陆公开站点优先策略。默认白名单包括：`people.com.cn`、`xinhuanet.com`、`cctv.com`、`chinanews.com.cn`、`thepaper.cn`、`caixin.com`、`yicai.com`、`eastmoney.com`、`cnstock.com`、`stcn.com`、`cs.com.cn`、`cls.cn`、`finance.sina.com.cn`、`finance.ifeng.com`、`qq.com`、`163.com`、`sohu.com`、`gov.cn`、`csrc.gov.cn`、`sse.com.cn`、`szse.cn` 等。
- 搜索来源属于公开网页/新闻检索来源，典型返回包括新闻媒体站点、财经网站、门户文章页、交易所/监管公告页、公开资讯页等；不直接覆盖需要登录或私有接口的微信、微博、小红书后台数据。
- Tavily 已支持 `topic="news"`、`time_range="year"`、`search_depth="advanced"` 等参数，适合过去一年公开新闻检索。
- 企查查新闻接口适合补充企业相关新闻、风险事件与工商关联语境，但通常按次计费，批量名单运行前请先确认套餐和额度。
- 企查查 MCP 已可配置到 Codex，但仍受同样的境外 IP 限制。要稳定启用企查查，建议将服务部署在中国大陆服务器或使用企查查认可的境内出口网络。
- DuckDuckGo 方式无需 Key，但稳定性可能受网络环境影响。
- LLM 报告生成依赖 `OPENAI_API_KEY`；如果使用兼容 OpenAI 协议的模型平台，可填写 `OPENAI_BASE_URL` 与自定义 `LLM_MODEL`。
- 代码已对“单个主体抓取失败”做异常隔离，不会导致整批任务中断；即使邮件发送失败，也会保留已生成的数据和报告文件。
