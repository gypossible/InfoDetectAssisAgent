from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import Flask, abort, jsonify, render_template_string, request, send_file, url_for
from werkzeug.utils import secure_filename

from opinion_monitor.config import Settings
from opinion_monitor.logging_utils import setup_logging
from opinion_monitor.models import PipelineProgress, PipelineResult
from opinion_monitor.pipeline import PublicOpinionPipeline
from opinion_monitor.runtime_info import get_app_version

settings = Settings()
settings.ensure_directories()
setup_logging(settings.log_dir)
APP_VERSION = get_app_version(settings.project_root)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WebTaskState:
    task_id: str
    source_path: str
    status: str = "queued"
    percent: int = 0
    stage: str = "queued"
    message: str = "任务准备中..."
    error: str = ""
    result: PipelineResult | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


TASKS: dict[str, WebTaskState] = {}
TASKS_LOCK = threading.Lock()

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
      --good: #1e675b;
      --bad: #b1462f;
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
      max-width: 1080px;
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
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
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
    button, .button-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 0;
      border-radius: 999px;
      background: var(--accent);
      color: white;
      padding: 12px 18px;
      font-size: 15px;
      cursor: pointer;
      text-decoration: none;
    }
    button:hover, .button-link:hover { background: var(--accent-dark); }
    .button-row {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 14px;
    }
    .status {
      margin-top: 18px;
      padding: 18px;
      border-radius: 16px;
      background: #fff7ef;
      border: 1px solid #f0d5bf;
    }
    .status.success {
      background: #f1fbf7;
      border-color: #c8eadf;
    }
    .status.error {
      background: #fff3f0;
      border-color: #f2cbc0;
    }
    .path {
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 13px;
      color: #6a4a36;
      word-break: break-all;
    }
    ul { padding-left: 18px; }
    .metrics {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin-top: 16px;
    }
    .metric {
      padding: 14px;
      border-radius: 14px;
      background: #fff;
      border: 1px solid var(--line);
    }
    .metric strong {
      display: block;
      font-size: 24px;
      margin-bottom: 6px;
    }
    progress {
      width: 100%;
      height: 18px;
      margin: 10px 0 4px;
      accent-color: var(--accent);
    }
    .small { font-size: 13px; color: var(--muted); }
    .hidden { display: none; }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>自动化舆情监测 Agent</h1>
      <p>推荐方式是直接上传一个 Excel 工作簿执行本次监测。系统会自动扫描该工作簿所有 sheet 的 B 列主体名称，并自动跳过“主体名称”“主体”“发行人名称”等常见表头。</p>
      <p>分析完成后，系统会额外生成一份“写回舆情后的 Excel”，把每个主体近一年最多 10 条舆情写到主体名称之后，各单元格一条。</p>
      <p class="path">辅助本地路径：{{ default_input_path }}</p>
      <p class="path">搜索源链路：{{ provider_chain }}</p>
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

    {% if task %}
      <section class="status" id="task-status-card">
        <strong id="task-title">执行中</strong>
        <p id="task-message">{{ task.message }}</p>
        <progress id="task-progress" max="100" value="{{ task.percent }}"></progress>
        <p class="small" id="task-stage">当前阶段：{{ task.stage }} | {{ task.percent }}%</p>
        <p class="small">任务编号：{{ task.task_id }}</p>
      </section>
    {% endif %}

    {% if message %}
      <section class="status {{ message_class }}">
        <strong>{{ message_title }}</strong>
        <p>{{ message }}</p>
        {% if result %}
          <div class="metrics">
            <div class="metric"><strong>{{ result.entity_count }}</strong>读取主体数</div>
            <div class="metric"><strong>{{ result.searched_entity_count }}</strong>实际搜索主体数</div>
            <div class="metric"><strong>{{ result.matched_entity_count }}</strong>命中舆情主体数</div>
            <div class="metric"><strong>{{ result.skipped_entity_count }}</strong>自动跳过主体数</div>
            <div class="metric"><strong>{{ result.failed_entity_count }}</strong>搜索失败主体数</div>
            <div class="metric"><strong>{{ result.article_count }}</strong>舆情条数</div>
          </div>
          <ul>
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
          {% if result.annotated_data_file_path %}
            <p class="path">写回舆情后的 Excel：{{ result.annotated_data_file_path }}</p>
          {% endif %}
          <div class="button-row">
            <a class="button-link" href="{{ url_for('download_artifact', task_id=result_task_id, artifact='data') }}">下载原始数据 Excel</a>
            <a class="button-link" href="{{ url_for('download_artifact', task_id=result_task_id, artifact='report') }}">下载分析报告</a>
            {% if result.annotated_data_file_path %}
              <a class="button-link" href="{{ url_for('download_artifact', task_id=result_task_id, artifact='annotated') }}">下载写回舆情后的 Excel</a>
            {% endif %}
          </div>
        {% endif %}
      </section>
    {% endif %}
  </div>

  {% if task %}
    <script>
      const taskId = "{{ task.task_id }}";
      const taskProgress = document.getElementById("task-progress");
      const taskMessage = document.getElementById("task-message");
      const taskStage = document.getElementById("task-stage");
      const taskTitle = document.getElementById("task-title");

      async function pollTaskStatus() {
        try {
          const response = await fetch("{{ url_for('task_status', task_id=task.task_id) }}", { cache: "no-store" });
          const payload = await response.json();
          taskProgress.value = payload.percent;
          taskMessage.textContent = payload.message;
          taskStage.textContent = `当前阶段：${payload.stage} | ${payload.percent}%`;

          if (payload.status === "completed") {
            taskTitle.textContent = "执行完成";
            window.location.href = payload.result_url;
            return;
          }
          if (payload.status === "failed") {
            taskTitle.textContent = "执行失败";
            taskMessage.textContent = payload.error;
            document.getElementById("task-status-card").classList.add("error");
            return;
          }
          setTimeout(pollTaskStatus, 1200);
        } catch (error) {
          taskMessage.textContent = `进度刷新失败：${error}`;
          setTimeout(pollTaskStatus, 2000);
        }
      }

      setTimeout(pollTaskStatus, 600);
    </script>
  {% endif %}
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


def _render_page(
    *,
    message: str = "",
    message_title: str = "",
    message_class: str = "",
    result: PipelineResult | None = None,
    result_task_id: str = "",
    task: WebTaskState | None = None,
):
    return render_template_string(
        HTML_TEMPLATE,
        default_input_path=str(settings.excel_input_path),
        provider_chain=",".join(settings.search_providers),
        app_version=APP_VERSION,
        excel_files=list_local_excel_files(),
        message=message,
        message_title=message_title,
        message_class=message_class,
        result=result,
        result_task_id=result_task_id,
        task=task,
    )


def _set_task_state(task_id: str, **updates) -> WebTaskState | None:
    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if task is None:
            return None
        for key, value in updates.items():
            setattr(task, key, value)
        return task


def _get_task(task_id: str) -> WebTaskState | None:
    with TASKS_LOCK:
        return TASKS.get(task_id)


def _create_task(source_path: Path) -> WebTaskState:
    task = WebTaskState(task_id=uuid4().hex, source_path=str(source_path))
    with TASKS_LOCK:
        TASKS[task.task_id] = task
    return task


def _run_task(task_id: str, source_path: Path) -> None:
    def _on_progress(update: PipelineProgress) -> None:
        _set_task_state(
            task_id,
            status="running",
            percent=update.percent,
            stage=update.stage,
            message=update.message,
        )

    try:
        _set_task_state(task_id, status="running", percent=2, stage="prepare", message="任务已创建，准备开始执行...")
        result = PublicOpinionPipeline(settings).run(
            excel_source=source_path,
            progress_callback=_on_progress,
        )
        _set_task_state(
            task_id,
            status="completed",
            percent=100,
            stage="done",
            message="执行完成，结果文件已生成。",
            result=result,
        )
    except Exception as exc:
        logger.exception("网页模式执行失败：%s", exc)
        _set_task_state(
            task_id,
            status="failed",
            percent=100,
            stage="failed",
            message="执行失败",
            error=f"{type(exc).__name__}: {exc}",
        )


@app.route("/", methods=["GET"])
def index():
    return _render_page()


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

        task = _create_task(selected_path)
        worker = threading.Thread(
            target=_run_task,
            args=(task.task_id, selected_path),
            name=f"pipeline-task-{task.task_id[:8]}",
            daemon=True,
        )
        worker.start()
        return _render_page(task=task)
    except Exception as exc:
        logger.exception("网页模式启动任务失败：%s", exc)
        return _render_page(
            message=f"{type(exc).__name__}: {exc}",
            message_title="执行失败",
            message_class="error",
        )


@app.route("/api/task/<task_id>", methods=["GET"])
def task_status(task_id: str):
    task = _get_task(task_id)
    if task is None:
        return jsonify({"status": "missing", "error": "任务不存在。"}), 404

    payload = {
        "task_id": task.task_id,
        "status": task.status,
        "percent": task.percent,
        "stage": task.stage,
        "message": task.message,
        "error": task.error,
        "result_url": url_for("view_result", task_id=task.task_id),
    }
    return jsonify(payload)


@app.route("/result/<task_id>", methods=["GET"])
def view_result(task_id: str):
    task = _get_task(task_id)
    if task is None:
        abort(404)
    if task.status == "completed" and task.result is not None:
        return _render_page(
            message="舆情数据已抓取完成，报告、原始数据以及写回舆情后的 Excel 已生成。",
            message_title="执行完成",
            message_class="success",
            result=task.result,
            result_task_id=task_id,
        )
    if task.status == "failed":
        return _render_page(
            message=task.error or "任务执行失败。",
            message_title="执行失败",
            message_class="error",
        )
    return _render_page(task=task)


@app.route("/download/<task_id>/<artifact>", methods=["GET"])
def download_artifact(task_id: str, artifact: str):
    task = _get_task(task_id)
    if task is None or task.result is None:
        abort(404)

    artifact_map = {
        "data": task.result.data_file_path,
        "report": task.result.report_file_path,
        "annotated": task.result.annotated_data_file_path,
    }
    target_path = artifact_map.get(artifact)
    if target_path is None:
        abort(404)
    target_path = Path(target_path)
    if not target_path.exists():
        abort(404)
    return send_file(target_path, as_attachment=True, download_name=target_path.name)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=7860, debug=False)
