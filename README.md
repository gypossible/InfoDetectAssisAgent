# 自动化舆情监测与报告生成 Agent

一个可直接扩展的 Python 项目，用于每天自动读取本地 Excel 监测名单，抓取过去 24 小时的新闻舆情，生成原始数据表与 AI 深度分析报告，并通过邮件发送给指定收件人。

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
- `SEARCH_PROVIDER`：默认 `tavily`，也支持 `duckduckgo`、`bing`、`serpapi`
- `TAVILY_API_KEY`：Tavily 搜索 Key
- `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `LLM_MODEL`：大模型配置
- `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD`：发件邮箱 SMTP 配置
- `EMAIL_RECIPIENTS`：收件人，默认已设置为 `liuguangyuan@natrust.cn`

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
- `TAVILY_API_KEY`、`OPENAI_API_KEY`、SMTP 账号密码等敏感信息请在 Render 后台环境变量中填写，不要提交到仓库

## 6. 输出内容

程序执行后会在 `outputs/YYYYMMDD/` 目录下生成：

- `舆情原始数据_YYYYMMDD.xlsx`
- `每日舆情分析报告_YYYYMMDD.md`

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
- `search_clients.py`：封装 `Tavily`、`DuckDuckGo`、`Bing News Search`、`SerpAPI` 搜索接口
- `data_processing.py`：清洗、去重、打标签并导出原始 Excel
- `report_generator.py`：调用 LLM 生成不少于 1500 字的 Markdown 舆情分析报告
- `email_dispatcher.py`：通过 SMTP 发送正文和附件
- `scheduler.py`：提供 `schedule` 常驻调度入口
- `pipeline.py`：串联完整业务流程
- `web_app.py`：提供本地网页上传和触发执行入口
- `streamlit_app.py`：提供免费公开部署用的 Streamlit 入口

## 9. 使用提醒

- Tavily 已支持 `topic="news"`、`time_range="day"`、`search_depth="advanced"` 等官方参数，适合近 24 小时新闻舆情检索。
- DuckDuckGo 方式无需 Key，但稳定性可能受网络环境影响。
- LLM 报告生成依赖 `OPENAI_API_KEY`；如果使用兼容 OpenAI 协议的模型平台，可填写 `OPENAI_BASE_URL` 与自定义 `LLM_MODEL`。
- 代码已对“单个主体抓取失败”做异常隔离，不会导致整批任务中断；即使邮件发送失败，也会保留已生成的数据和报告文件。
