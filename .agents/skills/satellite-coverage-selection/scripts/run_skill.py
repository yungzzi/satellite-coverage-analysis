# -*- coding: utf-8 -*-
"""仓库级 Skill 的稳定启动器。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from siriagent_skill.runner import main


if __name__ == "__main__":
    raise SystemExit(main(["--project-root", str(PROJECT_ROOT), *sys.argv[1:]]))
