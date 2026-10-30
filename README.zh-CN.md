<p align="center"><img src="https://raw.githubusercontent.com/Hybirdss/kicad-cli-extended/main/docs/assets/banner.png" alt="kicad-cli-extended" width="820"></p>

<div align="center">

# kicad-cli-extended (`kce`)

**面向 AI 智能体与硬件自动化扩展的 KiCad 官方 `kicad-cli` 工具**

[![CI](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml/badge.svg)](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/pyproject.toml)

[English](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.md) | [한국어](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.ko.md) | **简体中文** | [繁體中文](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.zh-TW.md) | [日本語](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.ja.md) | [Español](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.es.md) | [हिन्दी](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.hi.md)

[性能基准](#性能基准) · [功能对比](#kicad-cli-与-kicad-cli-extended-对比) · [安装](#环境要求与安装) · [快速开始](#制作你的第一块-led-电路板) · [智能体技能](#ai-智能体技能) · [工作流](#工作流) · [命令组](#命令组) · [JSON 契约](#json-输出契约) · [CI 与 PowerShell](#powershell-与-ci)

</div>

> **正在使用 Altium Designer？** 请查看姊妹工具 **[altium-cli-extended (`ace`)](https://github.com/Hybirdss/altium-cli-extended)**。两个项目均采用 GPL-3.0-or-later 许可证。

**`kce`** (KiCad CLI Extended) 完整透传官方 `kicad-cli` 的全部命令与选项，并在此基础上扩展了原理图编辑、网表驱动电路构建（`sch apply`/`verify`）、PCB 同步更新、布局评分（`pcb score`）、布线与自动布线、制造打包（JLCPCB/PCBWay）、版本差异对比、原子事务执行计划以及 AI 智能体技能（Skill）——所有输出均遵循严格的 JSON 契约。

它直接驱动真实的 **KiCad 10**（`kicad-cli` 与 KiCad 内置的 `pcbnew` Python 模块），并在每次写入文件后通过 `kicad-cli` 重新加载验证，确保每一次修改都经过 KiCad 引擎本身的校验。

---

## 性能基准

`kce` 常驻加载的技能元数据（frontmatter）仅占 **157 个 Token**（加载完整 `SKILL.md` 为 3,737 个 Token），而 2026-10-28 按 GitHub Star 数选出的两个主流公开 KiCad MCP 服务器仅工具定义就需要消耗 **4,927–63,420 个 Token**。完整命令参考手册（`COMMANDS.md`）按需打开时为 11,238 个 Token。

测试于 2026-10-30 在 16 核 Linux 机器上完成（KiCad 10.0.7、Python 3.11.17、`tiktoken` 0.14.0 `o200k_base`）：在 KiCad 官方 `pic_programmer` 演示项目的全新副本上进行 5 次交错重复实验，每次实验启动时系统负载均低于 4.00 门限（记录负载：2.02、3.38、3.54、3.45、3.36）。Token 取请求加响应的中位数，耗时取挂钟时间（wall clock）中位数。详见[测试方法](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/METHOD.md)、[汇总与区间](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/SUMMARY-2026-10-30.md)及[全部原始记录](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/results-2026-10-30.json)。

| 方案 | 暴露工具数 | 工作开始前的上下文 Token |
|---|---:|---:|
| **`kce`（技能 frontmatter）** | 1 个命令 | **157** |
| `kce`（完整 `SKILL.md`，含 frontmatter） | 1 个命令 | 3,737 |
| `mixelpixx/KiCAD-MCP-Server` 2.8.3（选取时 2,995★），全部工具 | 244 | **63,420** |
| `mixelpixx`，`core-toolbox` 配置 | 37 | **6,445** |
| `lamaalrajih/kicad-mcp`（选取时 531★） | 16 | **4,927** |

| 任务（基于 `pic_programmer` 演示项目） | `kce` (Token / 耗时) | `mixelpixx` (Token / 耗时) | `lamaalrajih` (Token / 耗时) |
|---|---|---|---|
| ERC 汇总 | 108 / 3.62 s | 69 / 3.09 s | 无此工具 |
| DRC 检查 | 119 / 4.45 s | 278 / 3.63 s | 109 / 3.40 s |
| BOM 导出 | 177 / 1.00 s | 111 / 0.59 s | 133 / 0.53 s |
| Gerber + 钻孔文件 | 2,771 / 2.15 s | 645 / 1.43 s | 无此工具 |

所有 50 次受支持的运行均成功在磁盘上生成了预期文件（BOM、Gerber、钻孔文件）或返回了准确的 ERC/DRC 违规计数。

---

## `kicad-cli` 与 `kicad-cli-extended` 对比

基于 KiCad 10.0.7 `kicad-cli` 的 `--help` 命令树（43 个命令，582 个选项；保存在 [`kicad_cli_spec.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/src/kicad_cli_extended/utils/kicad_cli_spec.json) 中）。

| 功能特性 | `kicad-cli` | `kicad-cli-extended` (`kce`) |
|---|:-:|:-:|
| 导出与检查（ERC、DRC、Gerber、钻孔、BOM、STEP、PDF、渲染等） | ✓ | ✓ 完整透传全部 43 个命令与所有选项 |
| 所有命令输出结构化 JSON | 仅 `erc`、`drc`、`export stats`、`import` 支持导出 JSON 报告文件 | ✓ 统一的 JSON 契约与错误分类 |
| 原理图编辑：放置符号、连线、网络标签、自动编号（Annotate） | | ✓ |
| 网表优先电路构建（`sch apply` / `sch verify`） | 仅支持导出网表 | ✓ 意图 JSON 应用、增量补丁与一致性校验 |
| 从原理图更新 PCB（`pcb update`） | 仅 `drc --schematic-parity` 可检查一致性 | ✓ 稳定 UUID、焊盘位移与走线检查 |
| 布局质量评分与有界合法化修复 | | ✓ `score`、`legalize`、`place near` |
| 交互式布线、自动布线、铺铜区（Zone）、板框定义 | 仅在 `drc` / 绘图时支持铺铜重填 | ✓ |
| 差分对、等长/偏斜规则与蛇形走线调谐 | 仅支持 DRC 检查 | ✓ 规则设置、耦合布线、蛇形等长（Meander） |
| 过孔围栏/缝合过孔（Via Stitching）、泪滴、规则区域 | | ✓ |
| 网络类（Netclass）与设计规则 | 只读 | ✓ 读写支持 |
| 制造压缩包（JLCPCB、PCBWay、OSH Park） | 仅支持 Jobset，无板厂预设 | ✓ 暂存区原子发布包与 BOM/CPL 门禁 |
| 预览图与版本对比（`project compare`） | 仅支持渲染与绘图 | ✓ 几何级差异对比、白名单（`--allow`）、可视化差异包 |
| 原子执行计划（`run`）与命名检查点（`session`） | | ✓ 提交前验收断言与状态回滚 |
| 常驻 JSON-RPC 工作进程（`serve`） | | ✓ 预热 `pcbnew` 工作进程、超时控制、请求取消 |
| 元件库搜索、自定义符号/封装向导与校验 | | ✓ 引脚/焊盘/3D 模型完整性检查 |
| 拼板（Panelization） | | ✓ 邮票孔（Mouse bites）与 V-Cut 独立拼板文件生成 |
| Altium / Eagle 等外部电路板导入 | ✓ 电路板转换 | ✓ 目标路径指定、回读校验与源文件保护报告 |
| 通过 KiCad IPC API 实时控制 GUI | | ✓ |
| SPICE 电路仿真（`ngspice`） | | ✓ |
| AI 智能体技能（`kce skill install`） | | ✓ |

---

## 环境要求与安装

* **KiCad 10.x**（自带 `kicad-cli` 及 `pcbnew` Python 模块）。
* **Python 3.10+**（用于运行 `kce` 本身；`pcbnew` 会在自动检测到的 KiCad 自带解释器中运行）。
* *注：* PLY 3D 导出需要基于 OCCT 7.7 及以上版本编译的 KiCad；旧版本仍可正常导出 STEP。

| 操作系统 | 安装 KiCad 10 |
|---|---|
| **Windows** | [官方安装包](https://www.kicad.org/download/windows/)、`choco install kicad` 或 `winget install KiCad.KiCad` |
| **macOS** | `brew install --cask kicad`（或下载 `.dmg`） |
| **Ubuntu / Debian** | `sudo add-apt-repository ppa:kicad/kicad-10.0-releases && sudo apt install kicad` |
| **Fedora / Arch** | `sudo dnf install kicad` / `sudo pacman -S kicad kicad-library` |
| **Flatpak** | `flatpak install flathub org.kicad.KiCad`（支持自动检测） |

从公开仓库安装 `kce`：

```bash
uv tool install git+https://github.com/Hybirdss/kicad-cli-extended      # 推荐
# 或
pipx install git+https://github.com/Hybirdss/kicad-cli-extended
```

检查环境并初始化默认元件库表（仅首次运行需要）：

```bash
kce --json info doctor          # 检查 kicad-cli、pcbnew 解释器、库目录："ok": true
kce lib init-global-tables      # 仅首次运行：初始化 KiCad 默认全局库表
```

---

## 制作你的第一块 LED 电路板

下面演示如何从零制作一块通过 2 针连接器供电的限流 LED 电路板（3.3 V 电源，330 Ω 电阻）。请在一个空目录下开始：

```bash
kce --json project new led --name led
cd led
kce --json sch add Device:LED --ref D1 --at 101.6,63.5 --angle 90 --value RED --footprint LED_SMD:LED_0805_2012Metric
kce --json sch add Device:R --ref R1 --at 76.2,63.5 --value 330 --footprint Resistor_SMD:R_0805_2012Metric
kce --json sch add Connector_Generic:Conn_01x02 --ref J1 --at 50.8,63.5 --mirror y --footprint Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical
kce --json sch label-pin J1:1 VCC
kce --json sch label-pin R1:1 VCC
kce --json sch label-pin R1:2 LED
kce --json sch label-pin D1:2 LED
kce --json sch label-pin D1:1 GND
kce --json sch label-pin J1:2 GND
kce --json check erc --fail-on-error
kce --json sch nets
kce --json pcb outline rect --at 100,100 --size 30,20
kce --json --dry-run pcb update --stable-uuids
kce --json pcb update --stable-uuids
kce --json pcb score
kce --json pcb autoroute --grid auto
kce --json pcb connectivity
kce --json check drc --fail-on-error
kce --json fab package --preset jlcpcb --fail-on-drc --no-step
```

**验证要点：**
* **3 个元件、3 条双引脚网络**，ERC/DRC 错误数为 0，无未连接网络或原理图/PCB 一致性错误。
* `pcb score` 报告无 Courtyard 重叠，无超出板框的元件。
* `fab package` 返回 `complete: true`、`failed_steps` 为空，且 `outputs/fab-jlcpcb/` 下生成了完整的制造文件列表。
* *制造提示：* 此示例为裸板（Bare-board）流程。如需 SMT 贴片组装，请先指定经过验证的供应商料号（如 LCSC 编号），并在下单前通过板厂预览工具检查 CPL 极性与方向。

---

## 审查工程变更（ECO）与发布生产包

对于已验证的原理图 ECO（在 `VCC` 网络的 `U2` 附近新增电容 `C10`），先为 PCB 创建检查点，预览同步影响，再将布局和布线范围限定在受影响的元件上：

```bash
kce --project board session checkpoint before-pcb-eco
kce --project board --dry-run pcb update --keep-extra --lock-existing
kce --project board pcb update --keep-extra --lock-existing
kce --project board pcb place near C10 --to U2 --net VCC --max-dist 6 --rotation auto
kce --project board pcb legalize --refs C10 --max-move 5
kce --project board --dry-run pcb autoroute --refs C10 --grid auto
kce --project board pcb autoroute --refs C10 --grid auto
kce --project board pcb connectivity
kce --project board check drc --fail-on-error
kce --project board project compare git:HEAD --geometry --allow C2,C10,net:VCC,net:GND --fail-on-unexpected
```

* **同步安全：** 应用同步前先检查预演结果中的 `replaced[].pads_moved`、`unmatched_pins` 与 `tracks_ending_on_old_pads`。若批准的封装替换改变了焊盘位置，则需去掉 `--lock-existing`。
* **范围门禁：** `project compare --fail-on-unexpected` 会在发现 `--allow` 白名单之外的修改时以退出码 `4` 拒绝通过。
* **贴片生产发布：** 发布前运行 `fab parts check --offline` 与 `fab bom-check --require MPN,LCSC,Footprint`，随后使用 `fab package --preset jlcpcb --require-lcsc --require-rev v1.3.0 --manifest --name-suffix "{rev}-{preset}" --fail-on-drc -o RELEASE` 打包。只有 `complete: true` 才代表所有输出均已就绪。

---

## AI 智能体技能

`kce` 内置了智能体技能包（`SKILL.md` + `COMMANDS.md`），可向编程智能体传授上述全部工作流与验证门禁。

```bash
kce skill install                                 # 安装到本机检测到的所有智能体 + ~/.agents/skills
kce skill install --target claude --target codex --dry-run
kce skill install --dir ./.claude/skills          # 安装到项目本地目录
kce skill status
```

支持目标：`agents`（`~/.agents/skills`）、`claude`（`~/.claude/skills`）、`codex`（`~/.codex/skills`）、`gemini`（`~/.gemini/skills`）或通过 `--dir` 指定任意目录。

---

## 工作流

[内置技能](https://github.com/Hybirdss/kicad-cli-extended/blob/main/skills/kicad-cli-extended/SKILL.md#choose-a-scenario-recipe)包含 12 条触发式工作流配方，完整步骤详见 [`docs/AGENT-RECIPES.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/AGENT-RECIPES.md)。由于 ERC/DRC 默认仅输出报告，请务必加上显式门禁参数（`--fail-on-error`、`--fail-on-drc`、`--strict`）。

| 目标 | 核心命令链 | 章节 |
|---|---|---|
| 从零画板并导出打样文件 | `project new` → `sch apply` → `pcb update` → `fab package` | [首块电路板与打样制造](#首块电路板与打样制造) |
| 基于网表 JSON 规范驱动多阶段设计 | `session checkpoint --immutable` → `run stage.json` | [基于规范的网表优先设计](#基于规范的网表优先设计) |
| 局部 ECO 变更与几何级门禁校验 | `pcb update --keep-extra --lock-existing` → `project compare` | [现有项目的工程变更（ECO）](#现有项目的工程变更eco) |
| 在 CI 中拦截 PR 并发布硬件版本 | `check erc` → `check drc` → `fab bom-check` → `fab package` | [持续集成（CI）与标签发布](#持续集成ci与标签发布) |
| 利用预热 `pcbnew` 进程搜索最佳布局 | `serve` → `run trial.json` → `session restore best` | [使用预热工作进程搜索布局](#使用预热工作进程搜索布局) |
| 创建并校验缺失的符号与封装 | `lib sym new` → `lib fp wizard` → `lib link-check` → `lib fp check` | [补充库中缺失的元件](#补充库中缺失的元件) |
| 外部电路板导入、SPICE 仿真、高速布线与拼板 | `project import` / `sim` / `pcb diff-pair` / `pcb panelize` | [外部导入、仿真、高速布线与拼板](#外部导入仿真高速布线与拼板) |

### 首块电路板与打样制造

按照上方 LED 快速开始流程操作，并为贴片组装选择已验证的供应商编号。对于 USB-C 等复杂接口，编写网表意图前应先核对连接器实际引脚定义与供电角色。连接器边缘开口需使用 `pcb move` 与 `pcb lock` 手动定位；板厂工艺极限需通过 `project rules` 与 `project netclass` 显式设置。

### 基于规范的网表优先设计

将批准的电气连接意图保存在项目内的 JSON 文件中（纳入初始检查点），而将执行计划与日志保存在项目目录之外。每个阶段通过带有验收断言的 `run` 计划原子执行，并在通过后创建不可变（`--immutable`）检查点：

```bash
kce --json -p sensor session checkpoint initial --immutable
kce --json -p sensor --dry-run run schematic-stage.json
kce --json -p sensor run schematic-stage.json
kce --json -p sensor session checkpoint schematic-ok --immutable
kce --json -p sensor run board-stage.json
```

### 现有项目的工程变更（ECO）

记录基线网表、封装/焊盘、布局评分与 ERC/DRC，创建 `pre-eco` 检查点。先执行原理图计划并通过严格意图校验（`sch verify --subset --strict`），再同步至 PCB。最后使用 `project compare checkpoint:pre-eco --geometry --allow ...` 检验变更是否严格受控，若出现回退则立即执行 `session restore pre-eco`。

### 持续集成（CI）与标签发布

使用 [PR 工作流模板](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-ci.yml)与[硬件发布工作流模板](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-release.yml)，固定工具版本、拉取基线提交（`fetch-depth: 0`）、设置 ERC/DRC 门限、对比版本差异、检查 BOM 字段/价格，并在打包前校验标题栏版本号。

### 使用预热工作进程搜索布局

创建不可变检查点 `baseline` 与 `best-000000` 以及可变检查点 `best`，保持前台 `kce serve` 进程运行。每次候选布局通过试验计划移动元件、校验网表意图并用 `expect` 断言检查 `pcb score`（要求零重叠、零出界），通过 DRC 后才更新 `best`，否则回滚至 `best`。

### 补充库中缺失的元件

先搜索标准化名称与裸露焊盘（Exposed Pad）变体（`lib sym search`、`lib fp search`），若无匹配再通过 `lib sym new`、`lib fp new` 或 `lib fp wizard` 在项目本地库中创建，并使用 `${KIPRJMOD}` 引用路径。放置前运行 `lib link-check` 与 `lib fp check`，对照数据手册核对引脚表与焊盘尺寸。

### 外部导入、仿真、高速布线与拼板

* **外部电路板导入：** 将 Altium/Eagle 电路板导入新目录，检查 `read_back` 与转换损失，并结合姊妹工具 [`ace`](https://github.com/Hybirdss/altium-cli-extended) 对照原始设计清单。
* **SPICE 仿真与高速布线：** 在临时项目中用 `sim` 验证滤波器与偏置电路；高速模块按“规则设置 → 差分对布线 → 短线蛇形调谐 → 过孔围栏/缝合 → 局部泪滴 → 铺铜重填 → DRC → 长度报告”顺序执行；最后使用 `pcb panelize` 输出独立拼板文件。

---

## 命令组

| 命令组 | 功能说明 |
|---|---|
| `project` | 从模板创建项目、导入外部电路板、项目信息与文件列表、文本变量、网络类、设计规则、库表、装配变体（Variant）、归档、版本对比 |
| `sch` | 放置/移动/旋转符号、字段修改、连线、网络标签、电源符号、悬空标志（no-connect）、层次原理图、自动编号、网表查询、意图 `apply`/`verify`；透传 `kicad-cli` `erc`、`export *`、`upgrade` |
| `pcb` | 电路板检查、从原理图同步更新、布局评分、位置合法化、就近放置、布线、自动布线、差分对、等长调谐、过孔缝合/围栏、泪滴、铺铜与禁布区、板框、层栈、3D 模型、拼板、条形码、安装孔、光学定位点、DSN/SES；透传 `kicad-cli` `drc`、`render`、`export *`、`import`、`upgrade` |
| `lib` | 搜索与查看符号/封装、封装推荐、创建符号/封装向导、引脚/焊盘一致性检查、便携式 3D 模型配置 |
| `check` | ERC、DRC（含铺铜重填与原理图一致性检查）、带修复建议的 `diagnose` |
| `fab` | 制造打包（`generic`、`jlcpcb`、`pcbway`、`oshpark`）、元件/BOM 检查、价格与版本号门禁、清单生成 |
| `jobset` | 创建并执行 `.kicad_jobset` 任务集文件 |
| `preview` | 基于 `kicad-cli` 渲染 SVG/PNG 预览图、版本差异对比包、实时预览会话 |
| `gui` | 通过 KiCad IPC API 操控运行中的 GUI：状态、选中项、执行动作、保存/重新加载/重填铺铜 |
| `sim` | 基于 `ngspice` 的 SPICE 仿真：仿真指令、模型绑定、网表生成与运行 |
| `session` | 会话状态、撤销/重做/历史记录、保存/丢弃、检查点创建/列表/恢复/删除 |
| `run` | 执行带提交前验收断言的原子多步 JSON 计划 |
| `serve` | 基于 stdio 的常驻 JSON-RPC 2.0 服务（预热 `pcbnew` 工作进程、超时控制与取消） |
| `errors` | 查询所有稳定错误类型、分类、退出码与恢复命令 |
| `raw` | 直接查询/编辑任意 KiCad S-expression 文件，或运行 `pcbnew` Python 代码 |
| `repl` | 交互式命令行 Shell（不带子命令时的默认模式） |
| `info` / `skill` | 环境诊断（`doctor`）、功能检测、版本、命令元数据与离线 JSON Schema / 安装 AI 智能体技能 |

---

## JSON 输出契约

* **成功（退出码 `0`）：** 传入 `--json`（可置于子命令前或后）时，`stdout` 输出单个 JSON 文档。
* **失败（退出码 `1`–`7`）：** `stderr` 输出符合 [`schemas/error.schema.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/error.schema.json) 的单个 JSON 错误对象。
* **错误分类与退出码：**

  | 分类 (`category`) | 退出码 | 含义 | 主要 `type` 值 |
  |---|:-:|---|---|
  | `input` | 2 | 参数错误、未知位号或引脚、文件缺失、输入格式错误、锁定对象 | `UsageError`, `KicadInputError`, `KeyError`, `ValueError`, `SexprError`, `FileNotFoundError`, `FileExistsError`, `ItemLocked`, `RuntimeError` |
  | `design` | 3 | 因违反设计规则或会导致错误连接而被拒绝 | `NetMergeError`, `NetSplitError`, `RouteBlocked`, `DesignMismatch`, `AcceptanceFailed`, `PlacementBlocked`, `JobsetFailed`, `KicadCheckFailed`, `RuntimeError` (ERC/DRC 失败) |
  | `validation` | 4 | KiCad 拒绝了保存的文件（修改已回滚），或对比门禁发现预期外改动 | `ValidationError` |
  | `crash` | 5 | `kicad-cli` 或 `pcbnew` 工作进程崩溃或返回非法数据 | `KicadCrash` |
  | `timeout` | 6 | `kicad-cli` 超过 `KCE_TIMEOUT`（默认 1800 秒）或工作进程超时 | `KicadTimeout`, `ToolTimeout` |
  | `environment` | 7 | 未找到 KiCad 或外部工具，或发生操作系统级 I/O 错误 | `KicadNotFound`, `OSError`, `RuntimeError` |
  | `internal` | 1 | `kce` 内部缺陷（请提交反馈），`details.traceback` 含调用栈 | `KicadError`, `RuntimeError`, `InternalError`, `TypeError`, `IndexError` |

---

## PowerShell 与 CI

在 PowerShell 中请使用参数数组传递公共选项，并用 `run` 计划代替 Shell 循环：

```powershell
$KCE = "--json", "-p", "led"
& kce @KCE info doctor
& kce @KCE session checkpoint before-eco --immutable
& kce @KCE --dry-run run eco.json
& kce @KCE run eco.json
# 含负号的逗号分隔参数请使用 '=' 连接：--rotate=-45,0,45
```

在运行 CI 前将以下规则加入 `.gitignore` 与 `.gitattributes`：

```gitignore
out/
outputs/
*.kicad_prl
~*.lck
*-backups/
fp-info-cache
.kicad-cli-extended/
```

```gitattributes
*.kicad_* text eol=lf
```

---

## 开发与测试

```bash
git clone https://github.com/Hybirdss/kicad-cli-extended && cd kicad-cli-extended
python -m pip install -e ".[test]"
python scripts/fetch_fixtures.py               # 下载端到端测试所需的 KiCad 演示项目
python -m pytest tests/test_core.py            # 单元测试（无需安装 KiCad）
python scripts/gen_skill.py --check            # 校验生成的技能/参考手册/Schema 是否同步
KCE_FORCE_INSTALLED=1 python -m pytest tests   # 针对真实 KiCad 运行完整测试套件
```

更多测试说明请参阅 [`docs/TESTING.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/TESTING.md)。

---

## 许可证

GPL-3.0-or-later。`kicad-cli-extended` 是独立的开源项目，与 KiCad 官方项目无隶属或背书关系。KiCad 是 KiCad 项目的商标。详见 [LICENSE](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE)。
