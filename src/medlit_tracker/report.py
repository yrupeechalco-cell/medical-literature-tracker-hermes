from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _label(record: dict[str, Any]) -> str:
    if record["record_type"] == "preprint":
        return "预印本，未经同行评议"
    if record["record_type"] == "trial":
        return "临床试验注册"
    return "同行评议论文"


def _warning(record: dict[str, Any]) -> str:
    status = str(record.get("status", "")).lower()
    if status == "retracted":
        return " **警告：已撤稿**"
    if status == "expression_of_concern":
        return " **警告：关注声明**"
    if status == "corrected":
        return " **提示：勘误/更正**"
    return ""


def compact_record(record: dict[str, Any], abstract_limit: int = 1600) -> dict[str, Any]:
    return {
        "canonical_id": record["canonical_id"],
        "version": record["version"],
        "record_type": record["record_type"],
        "evidence_label": _label(record),
        "title": record["title"],
        "abstract": record.get("abstract", "")[:abstract_limit],
        "authors": record.get("authors", [])[:8],
        "journal": record.get("journal", ""),
        "publication_date": record.get("publication_date"),
        "updated_date": record.get("updated_date"),
        "study_type": record.get("study_type", ""),
        "status": record.get("status", "active"),
        "score": record.get("score", 0),
        "match_reasons": record.get("match_reasons", []),
        "url": record["url"],
    }


def render_batch(batch: dict[str, Any], topic: dict[str, Any], reports_root: Path) -> dict[str, str]:
    reports_root.mkdir(parents=True, exist_ok=True)
    day_dir = reports_root / datetime.now().strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    compact = [compact_record(record) for record in batch["records"]]
    payload = {
        "batch_id": batch["batch_id"],
        "topic": {
            "topic_id": topic["topic_id"],
            "name_zh": topic["name_zh"],
            "description_zh": topic["description_zh"],
        },
        "records": compact,
    }
    json_path = day_dir / f"{batch['batch_id']}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# 医学文献追踪报告：{topic['name_zh']}",
        "",
        f"批次：`{batch['batch_id']}`",
        "",
        f"本批次共 {len(compact)} 条记录。系统用于科研筛选，不构成临床建议。",
        "",
    ]
    for index, record in enumerate(compact, 1):
        lines.extend(
            [
                f"## {index}. {record['title']}",
                "",
                f"- 类型：{record['evidence_label']}{_warning(record)}",
                f"- 来源：{record['journal'] or '未标注'}",
                f"- 日期：{record['publication_date'] or record['updated_date'] or '未标注'}",
                f"- 优先级分数：{record['score']}",
                f"- 匹配原因：{'; '.join(record['match_reasons']) or '主题关键词匹配'}",
                f"- 原始链接：{record['url']}",
                "",
                record["abstract"] or "无摘要。",
                "",
            ]
        )
    md_path = day_dir / f"{batch['batch_id']}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}

