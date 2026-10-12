from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


def _serialize(obj: Any) -> Any:
    if is_dataclass(obj):
        return {k: _serialize(v) for k, v in asdict(obj).items()}
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, list):
        return [_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def write_report(reports_dir: Path, name: str, payload: Any) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = reports_dir / f"{name}_{stamp}.json"
    md_path = reports_dir / f"{name}_{stamp}.md"

    data = _serialize(payload)
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    md_path.write_text(_to_markdown(name, data), encoding="utf-8")
    return md_path


def _to_markdown(title: str, data: dict[str, Any]) -> str:
    lines = [f"# {title.replace('_', ' ').title()}", ""]
    for key, value in data.items():
        lines.append(f"## {key}")
        if isinstance(value, (dict, list)):
            lines.append("```json")
            lines.append(json.dumps(value, indent=2))
            lines.append("```")
        else:
            lines.append(str(value))
        lines.append("")
    return "\n".join(lines)
