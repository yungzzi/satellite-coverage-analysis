# 运行环境与移交

只有在新的机器、Agent 环境或 Skill 安装位置首次使用时读取本文件。业务参数仍以
`input-output-schema.md` 为准。

## Skill 与引擎的关系

本目录提供 Agent 发现、追问、调用和解释规则；实际物理计算依赖完整的“卫星覆盖性分析”项目，
包括 `src/` 引擎、城市边界、平台目录和 TLE。只复制 Skill 文件夹而不提供引擎仓库，不能完成计算。

## 三种本地使用方式

1. **项目内 Skill**：保留在项目 `.agents/skills/` 中，不需要配置；启动器按自身位置找到项目根目录。
2. **全局 Skill**：把本目录安装到 Agent 的全局 Skills 目录后，设置环境变量
   `SATELLITE_COVERAGE_PROJECT_ROOT` 指向完整项目根目录。
3. **显式指定**：临时调用时传 `--project-root <完整项目根目录>`。显式参数优先于环境变量。

启动器还会从当前工作目录向上寻找项目，因此 Agent 在项目任意子目录工作时也能直接调用。

## 首次自检

```powershell
python scripts/run_skill.py --self-check --pretty
```

若 Skill 位于全局目录：

```powershell
$env:SATELLITE_COVERAGE_PROJECT_ROOT = 'D:\path\to\卫星覆盖性分析'
python scripts/run_skill.py --self-check --pretty
```

自检核对：核心引擎文件、浙江边界、平台目录、固定 TLE、Python 依赖和 PuLP/CBC 求解器。
只有 `status=ok`、`ready_for_direct_agent_use=true` 才表示覆盖与五项选星都能直接运行。

## 移交边界

- 另一名 Codex/本地 Agent：只要能执行 Python、能读取完整项目并通过自检，即可直接使用。
- SIRIAgent 等自研平台：需按平台规范登记 Skill slug、参数和脚本入口，但业务 JSON 与引擎无需重写。
- 不能执行本地 Python 的云端 Agent：仅复制 Skill 无效，需要 HTTP、MCP 或该平台自己的工具适配层。

上述差别是运行环境差别，不是覆盖算法能力差别。
