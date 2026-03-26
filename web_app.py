from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template_string, request, url_for
from werkzeug.utils import secure_filename

from opinion_monitor.config import Settings
from opinion_monitor.logging_utils import setup_logging
from opinion_monitor.pipeline import PublicOpinionPipeline
from opinion_monitor.runtime_info import get_app_version

settings = Settings()
settings.ensure_directories()
setup_logging(settings.log_dir)
APP_VERSION = get_app_version(settings.project_root)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
logger = logging.getLogger(__name__)

HTML_TEMPLATE = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>舆情监测 Agent</title>
  <style>
    :root {
      --bg: #f4efe7;
      --card: #fffdf8;
      --ink: #1f2a2e;
      --muted: #5b6b72;
      --accent: #b84c2a;
      --accent-dark: #8f3519;
      --line: #e5d7c8;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "PingFang SC", "Noto Sans SC", sans-serif;
      background:
        radial-gradient(circle at top right, rgba(184,76,42,.13), transparent 28%),
        radial-gradient(circle at bottom left, rgba(30,103,91,.10), transparent 25%),
        var(--bg);
      color: var(--ink);
    }
    .wrap {
      max-width: 960px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }
    .hero {
      padding: 28px;
      background: linear-gradient(135deg, #fff9f2, #fef6ed);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: 0 10px 30px rgba(79, 62, 43, 0.08);
    }
    h1 { margin: 0 0 12px; font-size: 32px; }
    p { line-height: 1.7; color: var(--muted); }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 18px;
      margin-top: 20px;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 22px;
    }
    label {
      display: block;
      font-weight: 700;
      margin-bottom: 8px;
    }
    select, input[type=file] {
      width: 100%;
      padding: 12px;
      border-radius: 14px;
      border: 1px solid #d8c5b3;
      background: white;
      margin-bottom: 14px;
    }
    button {
      border: 0;
      border-radius: 999px;
      background: var(--accent);
      color: white;
      padding: 12px 18px;
      font-size: 15px;
      cursor: pointer;
    }
    button:hover { background: var(--accent-dark); }
    .status {
      margin-top: 18px;
      padding: 18px;
      border-radius: 16px;
      background: #fff7ef;
      border: 1px solid #f0d5bf;
    }
    .path {
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 13px;
      color: #6a4a36;
      word-break: break-all;
    }
    ul { padding-left: 18px; }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>自动化舆情监测 Agent</h1>
      <p>推荐方式是直接上传一个 Excel 工作簿执行本次监测。系统会自动扫描该工作簿所有 sheet 的 B 列主体名称，并自动跳过“主体名称”“主体”“发行人名称”等常见表头。</p>
      <p class="path">辅助本地路径：{{ default_input_path }}</p>
      <p class="path">当前版本：{{ app_version }}</p>
    </section>

    <div class="grid">
      <section class="card">
        <h3>推荐方式：上传 Excel</h3>
        <form method="post" action="{{ url_for('run_pipeline') }}" enctype="multipart/form-data">
          <label for="upload_file">上传 Excel 文件</label>
          <input type="file" name="upload_file" id="upload_file" accept=".xlsx,.xlsm,.xltx,.xltm" required>
          <button type="submit">上传并执行</button>
        </form>
      </section>

      <section class="card">
        <h3>辅助方式：本地已有 Excel</h3>
        <p>如需复用本机已有文件，可从下方明确选择一个 Excel 工作簿执行；此入口不再默认整目录扫描。</p>
        <form method="post" action="{{ url_for('run_pipeline') }}">
          <label for="existing_file">检测到的本地 Excel</label>
          {% if not excel_files %}
            <p>当前辅助路径下未检测到可用 Excel 文件，请优先使用上传方式。</p>
          {% endif %}
          <select name="existing_file" id="existing_file" required>
            <option value="" selected disabled>请选择一个本地 Excel 文件</option>
            {% for file_path in excel_files %}
              <option value="{{ file_path }}">{{ file_path }}</option>
            {% endfor %}
          </select>
          <button type="submit" {% if not excel_files %}disabled{% endif %}>使用本地 Excel 执行</button>
        </form>
      </section>
    </div>

    {% if message %}
      <section class="status">
        <strong>{{ message_title }}</strong>
        <p>{{ message }}</p>
        {% if result %}
          <ul>
            <li>读取主体数：{{ result.entity_count }}</li>
            <li>实际搜索主体数：{{ result.searched_entity_count }}</li>
            <li>命中舆情主体数：{{ result.matched_entity_count }}</li>
            <li>自动跳过主体数：{{ result.skipped_entity_count }}</li>
            <li>搜索失败主体数：{{ result.failed_entity_count }}</li>
            <li>舆情条数：{{ result.article_count }}</li>
            <li>邮件是否发送成功：{{ "是" if result.email_sent else "否" }}</li>
          </ul>
          {% if result.warnings %}
            <ul>
              {% for warning in result.warnings %}
                <li>{{ warning }}</li>
              {% endfor %}
            </ul>
          {% endif %}
          <p class="path">原始数据：{{ result.data_file_path }}</p>
          <p class="path">分析报告：{{ result.report_file_path }}</p>
        {% endif %}
      </section>
    {% endif %}
  </div>
</body>
</html>
"""


def list_local_excel_files() -> list[str]:
    input_path = settings.excel_input_path
    if input_path.is_file():
        return [str(input_path)]
    if not input_path.exists():
        return []
    files = [
        path
        for path in sorted(input_path.iterdir())
        if path.is_file() and path.suffix.lower() in {".xlsx", ".xlsm", ".xltx", ".xltm"}
    ]
    return [str(path) for path in files[: settings.max_excel_files]]


@app.route("/", methods=["GET"])
def index():
    return render_template_string(
        HTML_TEMPLATE,
        default_input_path=str(settings.excel_input_path),
        app_version=APP_VERSION,
        excel_files=list_local_excel_files(),
        message="",
        message_title="",
        result=None,
    )


@app.route("/healthz", methods=["GET"])
def healthz():
    return {"status": "ok"}, 200


@app.route("/run", methods=["POST"])
def run_pipeline():
    uploaded_file = request.files.get("upload_file")
    existing_file = (request.form.get("existing_file") or "").strip()
    selected_path: Path | None = None

    try:
        if uploaded_file and uploaded_file.filename:
            original_name = uploaded_file.filename
            suffix = Path(original_name).suffix or ".xlsx"
            filename = secure_filename(original_name) or f"upload{suffix}"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            selected_path = settings.excel_upload_dir / f"{timestamp}_{filename}"
            uploaded_file.save(selected_path)
        elif existing_file:
            selected_path = Path(existing_file)
        else:
            raise ValueError("请上传一个 Excel 文件，或在辅助模式下明确选择一个本地 Excel 文件。")

        result = PublicOpinionPipeline(settings).run(excel_source=selected_path)
        message_title = "执行完成"
        message = "舆情数据已抓取完成，报告与原始数据已经生成。"
    except Exception as exc:
        logger.exception("网页模式执行失败：%s", exc)
        result = None
        message_title = "执行失败"
        message = f"{type(exc).__name__}: {exc}"

    return render_template_string(
        HTML_TEMPLATE,
        default_input_path=str(settings.excel_input_path),
        app_version=APP_VERSION,
        excel_files=list_local_excel_files(),
        message=message,
        message_title=message_title,
        result=result,
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=7860, debug=False)
