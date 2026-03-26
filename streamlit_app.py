from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import streamlit as st

from opinion_monitor.config import Settings
from opinion_monitor.logging_utils import setup_logging
from opinion_monitor.pipeline import PublicOpinionPipeline
from opinion_monitor.runtime_info import get_app_version

settings = Settings()
settings.ensure_directories()
setup_logging(settings.log_dir)
APP_VERSION = get_app_version(settings.project_root)

st.set_page_config(
    page_title="舆情监测 Agent",
    page_icon="📰",
    layout="wide",
)

st.title("自动化舆情监测与报告生成 Agent")
st.caption("默认以上传单个 Excel 工作簿为主，适合部署到 Streamlit Community Cloud 的免费公开入口。")

with st.sidebar:
    st.subheader("使用说明")
    st.write("1. 上传一个 Excel 工作簿")
    st.write("2. 系统自动扫描所有 Sheet 的 B 列主体")
    st.write("3. 抓取过去一年的舆情并生成报告")
    st.write("4. 默认优先中国大陆公开新闻与公告站点")
    st.write("5. 即使邮件失败，也可以直接下载结果文件")

st.info("部署到云端前，请在平台 Secrets 中填写 Tavily、OpenAI 与 SMTP 配置。")

uploaded_file = st.file_uploader(
    "上传监测 Excel 文件",
    type=["xlsx", "xlsm", "xltx", "xltm"],
    help="系统会遍历所有 Sheet，从 B 列提取主体名称，并自动跳过“主体名称”“主体”“发行人名称”等常见表头。",
)


def _persist_upload(file_obj) -> Path:
    suffix = Path(file_obj.name).suffix or ".xlsx"
    temp_dir = Path(tempfile.mkdtemp(prefix="opinion_monitor_"))
    target_path = temp_dir / f"uploaded{suffix}"
    target_path.write_bytes(file_obj.getbuffer())
    return target_path


if "run_result" not in st.session_state:
    st.session_state.run_result = None
if "run_error" not in st.session_state:
    st.session_state.run_error = ""

if st.button("开始分析", type="primary", disabled=uploaded_file is None):
    st.session_state.run_result = None
    st.session_state.run_error = ""

    if uploaded_file is None:
        st.warning("请先上传 Excel 文件。")
    else:
        with st.spinner("正在抓取舆情、生成报告，请稍候..."):
            try:
                excel_path = _persist_upload(uploaded_file)
                st.session_state.run_result = PublicOpinionPipeline(settings).run(
                    excel_source=excel_path
                )
            except Exception as exc:
                st.session_state.run_error = f"{type(exc).__name__}: {exc}"

if st.session_state.run_error:
    st.error(f"执行失败：{st.session_state.run_error}")

result = st.session_state.run_result
if result is not None:
    col1, col2, col3 = st.columns(3)
    col1.metric("监测主体数", result.entity_count)
    col2.metric("实际搜索主体", result.searched_entity_count)
    col3.metric("舆情条数", result.article_count)

    extra_col1, extra_col2, extra_col3 = st.columns(3)
    extra_col1.metric("命中主体", result.matched_entity_count)
    extra_col2.metric("自动跳过主体", result.skipped_entity_count)
    extra_col3.metric("搜索失败主体", result.failed_entity_count)

    st.metric("邮件发送", "成功" if result.email_sent else "失败")

    st.success("分析完成，下面可以直接下载生成文件。")
    for warning in result.warnings:
        st.warning(warning)
    st.code(str(result.data_file_path), language="text")
    st.code(str(result.report_file_path), language="text")

    data_bytes = Path(result.data_file_path).read_bytes()
    report_bytes = Path(result.report_file_path).read_bytes()

    download_col1, download_col2 = st.columns(2)
    with download_col1:
        st.download_button(
            label="下载原始数据 Excel",
            data=data_bytes,
            file_name=Path(result.data_file_path).name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with download_col2:
        st.download_button(
            label="下载分析报告 Markdown",
            data=report_bytes,
            file_name=Path(result.report_file_path).name,
            mime="text/markdown",
        )

st.divider()
st.markdown(
    f"默认搜索源：`{settings.search_provider}`  |  版本：`{APP_VERSION}`  |  时间：`{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
)
