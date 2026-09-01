# -*- coding: utf-8 -*-
"""Skill 的 JSON 入口；既可被平台 import，也可作为本地命令运行。"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from .facade import execute_request
from .schemas import (
    PROJECT_ROOT,
    SkillInputError,
    SkillNeedsInput,
    describe_capabilities,
    normalize_request,
)


def run_skill(
    payload: Mapping[str, Any] | None,
    *,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """处理一份 ``inputs`` JSON，并始终返回可序列化状态。"""

    project_root = Path(root).resolve() if root else PROJECT_ROOT
    data = dict(payload or {})
    action = str(data.get("action", "")).strip().lower()
    raw_operation = str(data.get("operation", data.get("task_type", ""))).strip().lower()
    if action == "describe" or raw_operation in {"describe", "capabilities"}:
        operation = data.get("describe_operation")
        try:
            return describe_capabilities(project_root, operation=operation)
        except SkillInputError as exc:
            return {"status": "invalid_input", "message": str(exc), **exc.details}
    try:
        request = normalize_request(data)
        return execute_request(request, root=project_root)
    except SkillNeedsInput as exc:
        operation = exc.operation
        details = dict(exc.details)
        capabilities = describe_capabilities(project_root, operation=operation)
        message = str(exc)
        if operation is None and "operation" in exc.missing_fields:
            selection_tasks = [
                item
                for item in capabilities["tasks"]
                if item["operation"] != "coverage_only"
            ]
            capabilities = {**capabilities, "tasks": selection_tasks}
            hints = {
                name: dict(value)
                for name, value in details.get("parameter_hints", {}).items()
            }
            if "operation" in hints:
                hints["operation"]["allowed"] = [
                    item["operation"] for item in selection_tasks
                ]
                hints["operation"]["description"] = (
                    "请选择任务①～⑤之一；coverage_only 只在用户明确要求不选星时使用。"
                )
            details["parameter_hints"] = hints
            details["selection_task_choices"] = selection_tasks
            message = "请选择一项选星任务（任务①～⑤）。"
        return {
            "status": "needs_input",
            "operation": operation,
            "message": message,
            **details,
            "capabilities": capabilities,
        }
    except SkillInputError as exc:
        return {
            "status": "invalid_input",
            "message": str(exc),
            **exc.details,
        }
    except Exception as exc:  # 平台不能只收到 Python traceback；细节由服务器日志保留。
        return {
            "status": "execution_error",
            "message": str(exc),
            "error_type": type(exc).__name__,
        }


def _read_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.describe:
        return {"action": "describe", "describe_operation": args.describe_operation}
    if args.input_json:
        return json.loads(args.input_json)
    if args.input_file:
        return json.loads(args.input_file.read_text(encoding="utf-8-sig"))
    if not sys.stdin.isatty():
        text = sys.stdin.read().strip()
        if text:
            return json.loads(text)
    return {"action": "describe", "describe_operation": args.describe_operation}


def main(argv: list[str] | None = None) -> int:
    # Windows 中文系统的默认 stdout 可能是 GBK，而城市边界元数据可能包含任意
    # Unicode；机器接口固定输出 UTF-8，避免 JSON 在写出阶段失败。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="卫星区域覆盖与五项选星 Skill")
    parser.add_argument("--describe", action="store_true", help="显示可用任务、参数、默认值和当前边界")
    parser.add_argument("--describe-operation", help="只显示某一任务的参数")
    parser.add_argument("--input-json", help="直接传入 JSON 字符串")
    parser.add_argument("--input-file", type=Path, help="从 UTF-8 JSON 文件读取 inputs")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT, help="项目根目录")
    parser.add_argument("--pretty", action="store_true", help="缩进输出 JSON，便于人工验收")
    args = parser.parse_args(argv)
    try:
        payload = _read_payload(args)
    except (OSError, json.JSONDecodeError) as exc:
        result = {"status": "invalid_input", "message": f"输入 JSON 读取失败：{exc}"}
    else:
        # 底层历史代码偶尔会打印诊断文字；截获后写 stderr，保证 stdout 永远只有一个 JSON。
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = run_skill(payload, root=args.project_root)
        diagnostic = captured.getvalue().strip()
        if diagnostic:
            print(diagnostic, file=sys.stderr)
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            allow_nan=False,
        )
    )
    if result.get("status") == "invalid_input":
        return 2
    if result.get("status") == "execution_error":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["describe_capabilities", "main", "run_skill"]
