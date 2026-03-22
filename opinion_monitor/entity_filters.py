from __future__ import annotations

import re

LEGAL_ENTITY_HINTS = (
    "公司",
    "集团",
    "银行",
    "证券",
    "保险",
    "信托",
    "基金",
    "租赁",
    "投资",
    "控股",
    "实业",
    "资本",
    "能源",
    "建设",
    "发展",
    "科技",
    "工业",
    "物流",
    "旅游",
    "环保",
    "交通",
    "水务",
    "地产",
)

OBVIOUS_BOND_CODE_PATTERN = re.compile(r"^\d{2}[A-Za-z0-9\u4e00-\u9fff]{1,14}\d{2,}$")


def should_search_entity(entity_name: str) -> tuple[bool, str]:
    normalized = str(entity_name).replace("\u3000", " ").strip()
    condensed = normalized.replace(" ", "")

    if not condensed:
        return False, "名称为空"
    if len(condensed) < 2:
        return False, "名称过短"
    if condensed.isdigit():
        return False, "纯数字条目"
    if _looks_like_bond_code(condensed):
        return False, "疑似债券简称或代码"
    return True, ""


def _looks_like_bond_code(entity_name: str) -> bool:
    if any(keyword in entity_name for keyword in LEGAL_ENTITY_HINTS):
        return False
    return OBVIOUS_BOND_CODE_PATTERN.fullmatch(entity_name) is not None
