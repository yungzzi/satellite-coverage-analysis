# 运行环境与移交

只有在新的机器、Agent 环境或 Skill 安装位置首次使用时读取本文件。业务参数仍以
`input-output-schema.md` 为准。

## Skill 与引擎的关系

本目录提供 Agent 发现、追问、调用和解释规则；实际物理计算依赖完整的“卫星覆盖性分析”项目，
包括 `src/` 引擎、城市边界、平台目录和 TLE。只复制 Skill 文件夹而不提供引擎仓库，不能完成计算。

## 三种本地使用方式

1. **项目内 Skill**：保留在项目 `.agents/skills/` 中，不需要配置；启动器按自身位置找到项目根目录。
2. **全局 Skill**：把本目录安装到 Codex 用户目录 `$HOME/.agents/skills/` 后，设置环境变量
   `SATELLITE_COVERAGE_PROJECT_ROOT` 指向完整项目根目录。Windows 当前用户通常对应
   `C:\Users\<用户名>\.agents\skills\`。
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

## 给另一个 Codex 使用

### 同一项目中的另一个任务

不需要复制文件。让 Codex 打开完整项目根目录或任一子目录；它会向上扫描到项目根目录的
`.agents/skills/satellite-coverage-selection/SKILL.md`。新任务中可显式输入：

```text
请用 $satellite-coverage-selection 分析宁波未来 8 小时并选星；先列出五项选星任务让我选择。
```

也可以自然描述需求，由 Codex 根据 Skill 的 `description` 自动匹配。Skill 更新后通常会自动发现；
如果列表没有刷新，重启 Codex。可在 Codex CLI/IDE 中用 `/skills` 或输入 `$` 检查。

### 同一台电脑、其他项目中的 Codex

推荐在**最终稳定的主工作树**合并完成后，将 Skill 链接到用户目录；不要链接到以后会删除的临时
开发工作树。Windows PowerShell 示例：

```powershell
$projectRoot = 'D:\path\to\卫星覆盖性分析'
$skillSource = Join-Path $projectRoot '.agents\skills\satellite-coverage-selection'
$skillParent = Join-Path $HOME '.agents\skills'
$skillTarget = Join-Path $skillParent 'satellite-coverage-selection'

New-Item -ItemType Directory -Path $skillParent -Force | Out-Null
New-Item -ItemType Junction -Path $skillTarget -Target $skillSource
[Environment]::SetEnvironmentVariable(
  'SATELLITE_COVERAGE_PROJECT_ROOT', $projectRoot, 'User'
)
```

如果目标目录已经存在，先检查它指向哪里，不要直接覆盖。完成后重启 Codex，再用项目虚拟环境验收：

```powershell
& "$projectRoot\.venv\Scripts\python.exe" `
  "$skillTarget\scripts\run_skill.py" --self-check --pretty
```

返回 `status=ok` 和 `ready_for_direct_agent_use=true` 后，其他项目中的 Codex 也能使用同一个 Skill。
符号链接/目录联接的好处是项目内 Skill 更新后，全局入口自动同步，不需要反复复制。

## 移植到另一台电脑或服务器

需要迁移的是“两层”，不能只迁 Skill：

```text
Agent
  ↓ 读取 Skill（说明、Schema、启动器）
完整引擎项目（src、边界、目录、TLE）
  ↓
Python 虚拟环境与依赖
```

推荐步骤：

1. 通过受控 Git 仓库、移动硬盘或压缩包复制完整项目；不要复制原电脑的 `.venv`、缓存和 `output/`。
2. 在目标机重新创建虚拟环境并安装 `requirements.txt`。
3. 直接让 Codex 打开该项目；如果希望所有项目都能调用，再把 Skill 链接/复制到目标用户的
   `$HOME/.agents/skills/`。
4. 设置 `SATELLITE_COVERAGE_PROJECT_ROOT`，运行 `--self-check`。
5. 先跑快速覆盖样例，再跑完整业务验收；两台机器应使用同一 TLE、边界和 Profile 哈希。

Windows 环境准备示例：

```powershell
Set-Location 'D:\path\to\卫星覆盖性分析'
py -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
& '.\.venv\Scripts\python.exe' `
  '.\.agents\skills\satellite-coverage-selection\scripts\run_skill.py' `
  --self-check --pretty
```

Linux 服务器示例：

```bash
cd /opt/satellite-coverage-analysis
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
export SATELLITE_COVERAGE_PROJECT_ROOT=/opt/satellite-coverage-analysis
./.venv/bin/python .agents/skills/satellite-coverage-selection/scripts/run_skill.py \
  --self-check --pretty
```

纯 Python 主引擎可以部署到 Linux；ATK 历史对拍代码不参与 Skill 服务。

## SIRIAgent、Coze 与正式分发

- **SIRIAgent 同机 runner**：不把 Skill 放进 Codex 用户目录，而是由平台研发注册 slug、参数和
  `script_id`，让它执行 `scripts/run_skill.py` 或 import `run_skill(inputs)`。
- **Coze/纯云端 Agent**：如果不能访问服务器文件系统、不能启动本项目 Python，只复制 Skill 无效；
  需要先把引擎暴露成 HTTP、MCP 或平台自定义工具。
- **跨团队正式分发**：OpenAI 官方建议把超出单一仓库的可复用 Skill 包装成 Plugin；Plugin 可同时
  携带一个或多个 Skill、MCP 配置和展示资源。当前研究院同机联调暂时不需要先做 Plugin。

OpenAI 当前路径、发现与分发口径见：<https://learn.chatgpt.com/docs/build-skills>。
