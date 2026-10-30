<p align="center"><img src="https://raw.githubusercontent.com/Hybirdss/kicad-cli-extended/main/docs/assets/banner.png" alt="kicad-cli-extended" width="820"></p>

<div align="center">

# kicad-cli-extended (`kce`)

**專為 AI 代理與硬體自動化擴充的 KiCad 官方 `kicad-cli` 工具**

[![CI](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml/badge.svg)](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/pyproject.toml)

[English](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.md) | [한국어](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.ko.md) | [简体中文](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.zh-CN.md) | **繁體中文** | [日本語](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.ja.md) | [Español](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.es.md) | [हिन्दी](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.hi.md)

[效能基準](#效能基準) · [功能對比](#kicad-cli-與-kicad-cli-extended-對比) · [安裝](#環境需求與安裝) · [快速開始](#製作你的第一塊-led-電路板) · [代理技能](#ai-代理技能) · [工作流程](#工作流程) · [指令群組](#指令群組) · [JSON 契約](#json-輸出契約) · [CI 與 PowerShell](#powershell-與-ci)

</div>

> **正在使用 Altium Designer？** 請參考姊妹工具 **[altium-cli-extended (`ace`)](https://github.com/Hybirdss/altium-cli-extended)**。兩個專案皆採用 GPL-3.0-or-later 授權條款。

**`kce`** (KiCad CLI Extended) 完整支援官方 `kicad-cli` 的所有指令與選項，並擴充了原理圖編輯、網表驅動電路建構（`sch apply`/`verify`）、PCB 同步更新、佈局評分（`pcb score`）、走線與自動走線、製造打包（JLCPCB/PCBWay）、版本差異比對、原子交易執行計畫，以及 AI 代理技能（Skill）——所有輸出均遵循嚴格的 JSON 契約。

它直接驅動真實的 **KiCad 10**（`kicad-cli` 與 KiCad 內建的 `pcbnew` Python 模組），並在每次寫入檔案後透過 `kicad-cli` 重新載入驗證，確保每一次修改都通過 KiCad 引擎本身的校驗。

---

## 效能基準

`kce` 常駐載入的技能前置元資料（frontmatter）僅佔 **157 個 Token**（載入完整 `SKILL.md` 為 3,737 個 Token），而 2026-10-28 依 GitHub Star 數選出的兩個公開 KiCad MCP 伺服器僅工具定義就需消耗 **4,927–63,420 個 Token**。完整指令參考手冊（`COMMANDS.md`）按需開啟時為 11,238 個 Token。

測試於 2026-10-30 在 16 核心 Linux 機器上完成（KiCad 10.0.7、Python 3.11.17、`tiktoken` 0.14.0 `o200k_base`）：在 KiCad 官方 `pic_programmer` 示範專案的全新副本上進行 5 次交錯重複測試，每次啟動時系統負載均低於 4.00 門檻（記錄負載：2.02、3.38、3.54、3.45、3.36）。詳見[測試方法](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/METHOD.md)、[摘要與範圍](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/SUMMARY-2026-10-30.md)及[全部原始記錄](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/results-2026-10-30.json)。

| 方案 | 暴露工具數 | 工作開始前的上下文 Token |
|---|---:|---:|
| **`kce`（技能 frontmatter）** | 1 個指令 | **157** |
| `kce`（完整 `SKILL.md`，含 frontmatter） | 1 個指令 | 3,737 |
| `mixelpixx/KiCAD-MCP-Server` 2.8.3（選取時 2,995★），全部工具 | 244 | **63,420** |
| `mixelpixx`，`core-toolbox` 設定 | 37 | **6,445** |
| `lamaalrajih/kicad-mcp`（選取時 531★） | 16 | **4,927** |

| 任務（基於 `pic_programmer` 示範專案） | `kce` (Token / 耗時) | `mixelpixx` (Token / 耗時) | `lamaalrajih` (Token / 耗時) |
|---|---|---|---|
| ERC 摘要 | 108 / 3.62 s | 69 / 3.09 s | 無此工具 |
| DRC 檢查 | 119 / 4.45 s | 278 / 3.63 s | 109 / 3.40 s |
| BOM 匯出 | 177 / 1.00 s | 111 / 0.59 s | 133 / 0.53 s |
| Gerber + 鑽孔檔 | 2,771 / 2.15 s | 645 / 1.43 s | 無此工具 |

所有 50 次受支援的執行均成功在磁碟上產生預期檔案（BOM、Gerber、鑽孔檔）或回傳準確的 ERC/DRC 違規計數。

---

## `kicad-cli` 與 `kicad-cli-extended` 對比

基於 KiCad 10.0.7 `kicad-cli` 的 `--help` 指令樹（43 個指令，582 個選項；儲存於 [`kicad_cli_spec.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/src/kicad_cli_extended/utils/kicad_cli_spec.json)）。

| 功能特性 | `kicad-cli` | `kicad-cli-extended` (`kce`) |
|---|:-:|:-:|
| 匯出與檢查（ERC、DRC、Gerber、鑽孔、BOM、STEP、PDF、渲染等） | ✓ | ✓ 完整透傳全部 43 個指令與所有選項 |
| 所有指令輸出結構化 JSON | 僅 `erc`、`drc`、`export stats`、`import` 支援匯出 JSON 報告檔 | ✓ 統一的 JSON 契約與錯誤分類 |
| 原理圖編輯：放置符號、連線、網路標籤、自動編號（Annotate） | | ✓ |
| 網表優先電路建構（`sch apply` / `sch verify`） | 僅支援匯出網表 | ✓ 意圖 JSON 套用、增量修補與一致性驗證 |
| 從原理圖更新 PCB（`pcb update`） | 僅 `drc --schematic-parity` 可檢查一致性 | ✓ 穩定 UUID、焊盤位移與既有走線檢查 |
| 佈局品質評分與有界合法化修復 | | ✓ `score`、`legalize`、`place near` |
| 互動式走線、自動走線、鋪銅區（Zone）、板框定義 | 僅在 `drc` / 繪圖時支援鋪銅重填 | ✓ |
| 差分對、等長/偏斜規則與蛇形走線調諧 | 僅支援 DRC 檢查 | ✓ 規則設定、耦合走線、蛇形等長（Meander） |
| 導孔圍欄/縫合導孔（Via Stitching）、淚滴、規則區域 | | ✓ |
| 網路類別（Netclass）與設計規則 | 唯讀 | ✓ 讀寫支援 |
| 製造壓縮包（JLCPCB、PCBWay、OSH Park） | 僅支援 Jobset，無板廠預設 | ✓ 暫存區原子發布包與 BOM/CPL 門檻檢查 |
| 預覽圖與版本比對（`project compare`） | 僅支援渲染與繪圖 | ✓ 幾何級差異比對、允許清單（`--allow`）、視覺差異包 |
| 原子執行計畫（`run`）與具名檢查點（`session`） | | ✓ 提交前驗收斷言與狀態還原 |
| 常駐 JSON-RPC 工作程序（`serve`） | | ✓ 預熱 `pcbnew` 工作程序、逾時控制、請求取消 |
| 元件庫搜尋、自訂符號/封裝精靈與驗證 | | ✓ 接腳/焊盤/3D 模型完整性檢查 |
| 拼板（Panelization） | | ✓ 郵票孔（Mouse bites）與 V-Cut 獨立拼板檔產生 |
| Altium / Eagle 等外部電路板匯入 | ✓ 電路板轉換 | ✓ 目標路徑指定、回讀驗證與原始檔保護報告 |
| 透過 KiCad IPC API 即時控制 GUI | | ✓ |
| SPICE 電路模擬（`ngspice`） | | ✓ |
| AI 代理技能（`kce skill install`） | | ✓ |

---

## 環境需求與安裝

* **KiCad 10.x**（內含 `kicad-cli` 及 `pcbnew` Python 模組）。
* **Python 3.10+**（用於執行 `kce` 本身；`pcbnew` 會在自動偵測到的 KiCad 內建直譯器中執行）。
* *備註：* PLY 3D 匯出需要以 OCCT 7.7 或更新版本編譯的 KiCad；舊版本仍可匯出 STEP。

| 作業系統 | 安裝 KiCad 10 |
|---|---|
| **Windows** | [官方安裝程式](https://www.kicad.org/download/windows/)、`choco install kicad` 或 `winget install KiCad.KiCad` |
| **macOS** | `brew install --cask kicad`（或下載 `.dmg`） |
| **Ubuntu / Debian** | `sudo add-apt-repository ppa:kicad/kicad-10.0-releases && sudo apt install kicad` |
| **Fedora / Arch** | `sudo dnf install kicad` / `sudo pacman -S kicad kicad-library` |
| **Flatpak** | `flatpak install flathub org.kicad.KiCad`（支援自動偵測） |

從公開儲存庫安裝 `kce`：

```bash
uv tool install git+https://github.com/Hybirdss/kicad-cli-extended      # 推薦
# 或
pipx install git+https://github.com/Hybirdss/kicad-cli-extended
```

檢查環境並初始化預設元件庫表（僅首次執行需要）：

```bash
kce --json info doctor          # 檢查 kicad-cli、pcbnew 直譯器、元件庫目錄："ok": true
kce lib init-global-tables      # 僅首次執行：初始化 KiCad 預設全域元件庫表
```

---

## 製作你的第一塊 LED 電路板

以下示範如何從零製作一塊透過 2 針連接器供電的限流 LED 電路板（3.3 V 電源、330 Ω 電阻）。請在一個空目錄下開始：

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

**驗證重點：**
* **3 個元件、3 條雙接腳網路**，ERC/DRC 錯誤數為 0，無未連接網路或原理圖/PCB 一致性錯誤。
* `pcb score` 報告無 Courtyard 重疊，無超出板框的元件。
* `fab package` 回傳 `complete: true`、`failed_steps` 為空，且 `outputs/fab-jlcpcb/` 下產生了完整的製造檔案清單。

---

## 審查工程變更（ECO）與發布生產包

對於已驗證的原理圖 ECO（在 `VCC` 網路的 `U2` 附近新增電容 `C10`），先為 PCB 建立檢查點，預覽同步影響，再將佈局和走線範圍限定在受影響的元件上：

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

* **同步安全：** 套用同步前先檢查預演結果中的 `replaced[].pads_moved`、`unmatched_pins` 與 `tracks_ending_on_old_pads`。
* **範圍門檻：** `project compare --fail-on-unexpected` 會在發現 `--allow` 清單以外的變更時以結束碼 `4` 拒絕通過。
* **貼片生產發布：** 發布前執行 `fab parts check --offline` 與 `fab bom-check --require MPN,LCSC,Footprint`，再以 `fab package --preset jlcpcb --require-lcsc --require-rev v1.3.0 --manifest --name-suffix "{rev}-{preset}" --fail-on-drc -o RELEASE` 打包。

---

## AI 代理技能

`kce` 內建代理技能包（`SKILL.md` + `COMMANDS.md`），可讓 AI 代理自動掌握上述工作流程與檢查門檻。

```bash
kce skill install                                 # 安裝至本機偵測到的所有代理 + ~/.agents/skills
kce skill install --target claude --target codex --dry-run
kce skill install --dir ./.claude/skills          # 專案本地安裝
kce skill status
```

---

## 工作流程

[內建技能](https://github.com/Hybirdss/kicad-cli-extended/blob/main/skills/kicad-cli-extended/SKILL.md#choose-a-scenario-recipe)包含 12 條觸發式工作流程配方，完整說明詳見 [`docs/AGENT-RECIPES.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/AGENT-RECIPES.md)。

| 目標 | 核心指令鏈 | 章節 |
|---|---|---|
| 從零畫板並匯出打樣檔案 | `project new` → `sch apply` → `pcb update` → `fab package` | [首塊電路板與打樣製造](#首塊電路板與打樣製造) |
| 基於網表 JSON 規範驅動多階段設計 | `session checkpoint --immutable` → `run stage.json` | [基於規範的網表優先設計](#基於規範的網表優先設計) |
| 局部 ECO 變更與幾何級門檻驗證 | `pcb update --keep-extra --lock-existing` → `project compare` | [既有專案的工程變更（ECO）](#既有專案的工程變更eco) |
| 在 CI 中把關 PR 並發布硬體版本 | `check erc` → `check drc` → `fab bom-check` → `fab package` | [持續整合（CI）與標籤發布](#持續整合ci與標籤發布) |
| 利用預熱 `pcbnew` 程序搜尋最佳佈局 | `serve` → `run trial.json` → `session restore best` | [使用預熱工作程序搜尋佈局](#使用預熱工作程序搜尋佈局) |
| 建立並驗證缺失的符號與封裝 | `lib sym new` → `lib fp wizard` → `lib link-check` → `lib fp check` | [補充元件庫中缺失的零件](#補充元件庫中缺失的零件) |
| 外部電路板匯入、SPICE 模擬、高速走線與拼板 | `project import` / `sim` / `pcb diff-pair` / `pcb panelize` | [外部匯入、模擬、高速走線與拼板](#外部匯入模擬高速走線與拼板) |

### 首塊電路板與打樣製造

依照上方 LED 快速開始流程操作，並為 SMT 組裝選取已驗證的供應商料號。連接器邊緣開口需使用 `pcb move` 與 `pcb lock` 手動定位；板廠製程極限需透過 `project rules` 與 `project netclass` 明確設定。

### 基於規範的網表優先設計

將核准的電氣連接意圖保存在專案內的 JSON 檔案中（納入初始檢查點），每個階段透過帶有驗收斷言的 `run` 計畫原子執行，並在通過後建立不可變（`--immutable`）檢查點：

```bash
kce --json -p sensor session checkpoint initial --immutable
kce --json -p sensor --dry-run run schematic-stage.json
kce --json -p sensor run schematic-stage.json
kce --json -p sensor session checkpoint schematic-ok --immutable
kce --json -p sensor run board-stage.json
```

### 既有專案的工程變更（ECO）

記錄基準網表、封裝/焊盤、佈局評分與 ERC/DRC，建立 `pre-eco` 檢查點。先執行原理圖計畫並通過嚴格意圖驗證（`sch verify --subset --strict`），再同步至 PCB，最後以 `project compare checkpoint:pre-eco --geometry --allow ...` 檢驗變更是否嚴格受控。

### 持續整合（CI）與標籤發布

使用 [PR 工作流程範本](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-ci.yml)與[硬體發布工作流程範本](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-release.yml)，固定工具版本、拉取基準提交（`fetch-depth: 0`）、設定 ERC/DRC 門檻、比對版本差異、檢查 BOM 欄位/價格，並在打包前驗證標題欄版本號。

### 使用預熱工作程序搜尋佈局

建立不可變檢查點 `baseline` 與 `best-000000` 以及可變檢查點 `best`，保持前景 `kce serve` 程序執行。每次候選佈局透過試驗計畫移動零件、驗證網表意圖並以 `expect` 斷言檢查 `pcb score`（要求零重疊、零出界），通過 DRC 後才更新 `best`。

### 補充元件庫中缺失的零件

先搜尋標準化名稱與裸露焊盤（Exposed Pad）變體（`lib sym search`、`lib fp search`），若無符合項目再透過 `lib sym new`、`lib fp new` 或 `lib fp wizard` 在專案本地庫中建立，並使用 `${KIPRJMOD}` 路徑。放置前執行 `lib link-check` 與 `lib fp check`。

### 外部匯入、模擬、高速走線與拼板

* **外部電路板匯入：** 將 Altium/Eagle 電路板匯入新目錄，檢查 `read_back` 與轉換損失，並結合姊妹工具 [`ace`](https://github.com/Hybirdss/altium-cli-extended) 對照原始設計清單。
* **SPICE 模擬與高速走線：** 在暫存專案中用 `sim` 驗證電路；高速模組依「規則設定 → 差分對走線 → 短線蛇形調諧 → 導孔圍欄/縫合 → 局部淚滴 → 鋪銅重填 → DRC → 長度報告」順序執行；最後使用 `pcb panelize` 輸出獨立拼板檔案。

---

## 指令群組

| 指令群組 | 功能說明 |
|---|---|
| `project` | 從範本建立專案、匯入外部電路板、專案資訊與檔案清單、文字變數、網路類別、設計規則、元件庫表、組裝變體（Variant）、封存、版本比對 |
| `sch` | 放置/移動/旋轉符號、欄位修改、連線、網路標籤、電源符號、未連接標記（no-connect）、階層原理圖、自動編號、網表查詢、意圖 `apply`/`verify`；透傳 `kicad-cli` `erc`、`export *`、`upgrade` |
| `pcb` | 電路板檢查、從原理圖同步更新、佈局評分、位置合法化、就近放置、走線、自動走線、差分對、等長調諧、導孔縫合/圍欄、淚滴、鋪銅與禁佈區、板框、層疊、3D 模型、拼板、條碼、安裝孔、光學定位點、DSN/SES；透傳 `kicad-cli` `drc`、`render`、`export *`、`import`、`upgrade` |
| `lib` | 搜尋與檢視符號/封裝、封裝建議、建立符號/封裝精靈、接腳/焊盤一致性檢查、可攜式 3D 模型設定 |
| `check` | ERC、DRC（含鋪銅重填與原理圖一致性檢查）、附帶修復建議的 `diagnose` |
| `fab` | 製造打包（`generic`、`jlcpcb`、`pcbway`、`oshpark`）、零件/BOM 檢查、價格與版本號門檻、清單產生 |
| `jobset` | 建立並執行 `.kicad_jobset` 工作集檔案 |
| `preview` | 基於 `kicad-cli` 渲染 SVG/PNG 預覽圖、版本差異比對包、即時預覽工作階段 |
| `gui` | 透過 KiCad IPC API 操控執行中的 GUI：狀態、選取項目、執行動作、儲存/重新載入/重填鋪銅 |
| `sim` | 基於 `ngspice` 的 SPICE 模擬：模擬指令、模型綁定、網表產生與執行 |
| `session` | 工作階段狀態、復原/重做/歷史記錄、儲存/捨棄、檢查點建立/清單/還原/刪除 |
| `run` | 執行附帶提交前驗收斷言的原子多步 JSON 計畫 |
| `serve` | 基於 stdio 的常駐 JSON-RPC 2.0 伺服器（預熱 `pcbnew` 工作程序、逾時控制與取消） |
| `errors` | 查詢所有穩定錯誤類型、分類、結束碼與復原指令 |
| `raw` | 直接查詢/編輯任意 KiCad S-expression 檔案，或執行 `pcbnew` Python 程式碼 |
| `repl` | 互動式命令列 Shell（不帶子指令時的預設模式） |
| `info` / `skill` | 環境診斷（`doctor`）、功能偵測、版本、指令元資料與離線 JSON Schema / 安裝 AI 代理技能 |

---

## JSON 輸出契約

* **成功（結束碼 `0`）：** 傳入 `--json` 時，`stdout` 輸出單一 JSON 文件。
* **失敗（結束碼 `1`–`7`）：** `stderr` 輸出符合 [`schemas/error.schema.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/error.schema.json) 的單一 JSON 錯誤物件。
* **錯誤分類與結束碼：**

  | 分類 (`category`) | 結束碼 | 含義 | 主要 `type` 值 |
  |---|:-:|---|---|
  | `input` | 2 | 參數錯誤、未知元件編號或接腳、檔案缺失、輸入格式錯誤、鎖定物件 | `UsageError`, `KicadInputError`, `KeyError`, `ValueError`, `SexprError`, `FileNotFoundError`, `FileExistsError`, `ItemLocked`, `RuntimeError` |
  | `design` | 3 | 因違反設計規則或會導致錯誤連接而被拒絕 | `NetMergeError`, `NetSplitError`, `RouteBlocked`, `DesignMismatch`, `AcceptanceFailed`, `PlacementBlocked`, `JobsetFailed`, `KicadCheckFailed`, `RuntimeError` (ERC/DRC 失敗) |
  | `validation` | 4 | KiCad 拒絕了儲存的檔案（修改已還原），或比對門檻發現預期外改動 | `ValidationError` |
  | `crash` | 5 | `kicad-cli` 或 `pcbnew` 工作程序崩潰或回傳非法資料 | `KicadCrash` |
  | `timeout` | 6 | `kicad-cli` 超過 `KCE_TIMEOUT`（預設 1800 秒）或工作程序逾時 | `KicadTimeout`, `ToolTimeout` |
  | `environment` | 7 | 找不到 KiCad 或外部工具，或發生作業系統級 I/O 錯誤 | `KicadNotFound`, `OSError`, `RuntimeError` |
  | `internal` | 1 | `kce` 內部錯誤（請回報），`details.traceback` 含呼叫堆疊 | `KicadError`, `RuntimeError`, `InternalError`, `TypeError`, `IndexError` |

---

## PowerShell 與 CI

在 PowerShell 中請使用參數陣列傳遞共用選項，並以 `run` 計畫取代 Shell 迴圈：

```powershell
$KCE = "--json", "-p", "led"
& kce @KCE info doctor
& kce @KCE session checkpoint before-eco --immutable
& kce @KCE --dry-run run eco.json
& kce @KCE run eco.json
# 含負號的逗號分隔參數請使用 '=' 連接：--rotate=-45,0,45
```

在執行 CI 前將以下規則加入 `.gitignore` 與 `.gitattributes`：

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

## 開發與測試

```bash
git clone https://github.com/Hybirdss/kicad-cli-extended && cd kicad-cli-extended
python -m pip install -e ".[test]"
python scripts/fetch_fixtures.py               # 下載端到端測試所需的 KiCad 示範專案
python -m pytest tests/test_core.py            # 單元測試（無需安裝 KiCad）
python scripts/gen_skill.py --check            # 驗證產生的技能/參考手冊/Schema 是否同步
KCE_FORCE_INSTALLED=1 python -m pytest tests   # 針對真實 KiCad 執行完整測試套件
```

更多測試說明請參閱 [`docs/TESTING.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/TESTING.md)。

---

## 授權條款

GPL-3.0-or-later。`kicad-cli-extended` 是獨立的開源專案，與 KiCad 官方專案無隸屬或背書關係。KiCad 是 KiCad 專案的商標。詳見 [LICENSE](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE)。
