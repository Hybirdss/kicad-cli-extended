<p align="center"><img src="https://raw.githubusercontent.com/Hybirdss/kicad-cli-extended/main/docs/assets/banner.png" alt="kicad-cli-extended" width="820"></p>

<div align="center">

# kicad-cli-extended (`kce`)

**AIエージェントとハードウェア自動化のために拡張された、KiCad公式 `kicad-cli` ツール**

[![CI](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml/badge.svg)](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/pyproject.toml)

[English](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.md) | [한국어](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.ko.md) | [简体中文](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.zh-CN.md) | [繁體中文](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.zh-TW.md) | **日本語** | [Español](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.es.md) | [हिन्दी](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.hi.md)

[ベンチマーク](#ベンチマーク) · [機能比較](#kicad-cli-と-kicad-cli-extended-の比較) · [インストール](#動作要件とインストール) · [クイックスタート](#最初のled基板を作る) · [エージェントスキル](#aiエージェントスキル) · [ワークフロー](#ワークフロー) · [コマンド群](#コマンドグループ) · [JSON契約](#json出力契約) · [CIとPowerShell](#powershellとci)

</div>

> **Altium Designerをお使いですか？** 姉妹ツールである **[altium-cli-extended (`ace`)](https://github.com/Hybirdss/altium-cli-extended)** をご覧ください。両プロジェクトとも GPL-3.0-or-later ライセンスで提供されています。

**`kce`** (KiCad CLI Extended) は、公式 `kicad-cli` のすべてのコマンドとオプションを完全に網羅しつつ、回路図編集、ネットリスト主導の回路構築（`sch apply`/`verify`）、PCB同期更新、配置品質スコアリング（`pcb score`）、配線・自動配線、製造用パッケージ生成（JLCPCB/PCBWay）、リビジョン差分比較、アトミックな実行プラン、そしてAIエージェント用スキルを追加した拡張CLIです。すべての出力は一貫したJSON契約に従います。

実際の **KiCad 10**（`kicad-cli` および KiCad 同梱の `pcbnew` Pythonモジュール）を直接駆動し、ファイルを書き込むたびに `kicad-cli` で再読み込みを行うため、すべての変更が KiCad 自身によって検証されます。

---

## ベンチマーク

`kce` が常時読み込むスキルのフロントマターはわずか **157 トークン**（`SKILL.md` 全体でも 3,737 トークン）です。一方、2026-10-28 時点で GitHub スター数上位2つの公開 KiCad MCP サーバーは、ツール定義だけで **4,927〜63,420 トークン**を消費します。完全なコマンドリファレンス（`COMMANDS.md`）はオンデマンドで開かれた場合のみ 11,238 トークンを使用します。

2026-10-30 に 16 CPU Linux マシン（KiCad 10.0.7、Python 3.11.17、`tiktoken` 0.14.0 `o200k_base`）で計測。KiCad 公式デモ `pic_programmer` のクリーンなコピーに対して 5 回インタリーブ実行しました（各実行開始時のシステム負荷はすべて 4.00 未満：2.02、3.38、3.54、3.45、3.36）。トークン数はリクエスト＋レスポンスの中央値、時間は実経過時間（wall clock）の中央値です。詳細は[計測手法](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/METHOD.md)、[サマリーと範囲](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/SUMMARY-2026-10-30.md)、[全実行ログ](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/results-2026-10-30.json)を参照してください。

| 手法 | 公開ツール数 | 作業開始前のコンテキストトークン数 |
|---|---:|---:|
| **`kce`（スキル frontmatter のみ）** | 1 コマンド | **157** |
| `kce`（`SKILL.md` 全体、frontmatter 含む） | 1 コマンド | 3,737 |
| `mixelpixx/KiCAD-MCP-Server` 2.8.3（選定時 2,995★）、全ツール | 244 | **63,420** |
| `mixelpixx`、`core-toolbox` 構成 | 37 | **6,445** |
| `lamaalrajih/kicad-mcp`（選定時 531★） | 16 | **4,927** |

| タスク（`pic_programmer` デモ） | `kce` (トークン / 時間) | `mixelpixx` (トークン / 時間) | `lamaalrajih` (トークン / 時間) |
|---|---|---|---|
| ERC サマリー | 108 / 3.62 s | 69 / 3.09 s | ツールなし |
| DRC チェック | 119 / 4.45 s | 278 / 3.63 s | 109 / 3.40 s |
| BOM 出力 | 177 / 1.00 s | 111 / 0.59 s | 133 / 0.53 s |
| ガーバー + ドリル出力 | 2,771 / 2.15 s | 645 / 1.43 s | ツールなし |

サポート対象の全 50 回の実行すべてが成功し、期待通りの出力ファイル（BOM、ガーバー、ドリル）または ERC/DRC 違反件数を返しました。

---

## `kicad-cli` と `kicad-cli-extended` の比較

KiCad 10.0.7 の `kicad-cli` の `--help` ツリー（43 コマンド、582 オプション；[`kicad_cli_spec.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/src/kicad_cli_extended/utils/kicad_cli_spec.json) に収録）に基づく比較です。

| 機能 | `kicad-cli` | `kicad-cli-extended` (`kce`) |
|---|:-:|:-:|
| エクスポート＆チェック（ERC、DRC、ガーバー、ドリル、BOM、STEP、PDF、レンダー等） | ✓ | ✓ 全 43 コマンド・全オプションをパススルー対応 |
| 全コマンドの構造化 JSON 出力 | `erc`、`drc`、`export stats`、`import` のレポートファイルのみ | ✓ 統一された JSON 契約とエラー分類 |
| 回路図編集：シンボル配置、配線、ラベル、アノテーション | | ✓ |
| ネットリスト主導の回路構築（`sch apply` / `sch verify`） | ネットリスト出力のみ | ✓ インテント JSON 適用、差分パッチ、同一性検証 |
| 回路図から PCB を更新（`pcb update`） | `drc --schematic-parity` によるチェックのみ | ✓ 固定 UUID 維持、パッド移動・既存配線チェック |
| 配置スコアリングと有界自動補正 | | ✓ `score`、`legalize`、`place near` |
| 配線、自動配線、ゾーン（ベタ銅箔）、基板外形 | `drc` / プロット時のゾーン再塗りつぶしのみ | ✓ |
| 差動ペア、等長/スキュー規則とチューニング | DRC チェックのみ | ✓ 規則設定、結合配線、ミアンダ（Meander）配線 |
| ビアフェンス/ビアステッチング、ティアドロップ、ルール領域 | | ✓ |
| ネットクラスとデザインルール | 読み取り専用 | ✓ 読み書き両対応 |
| 製造パッケージ（JLCPCB、PCBWay、OSH Park） | Jobset のみ、ベンダープリセットなし | ✓ ステージング検証付きのリリース生成と BOM/CPL ゲート |
| プレビューとリビジョン比較（`project compare`） | レンダーとプロットのみ | ✓ ジオメトリ差分、許可リスト（`--allow`）、差分バンドル |
| アトミックな実行プラン（`run`）と名前付きチェックポイント（`session`） | | ✓ コミット前受け入れ条件判定と復元 |
| 常駐型 JSON-RPC ワーカー（`serve`） | | ✓ ウォーム `pcbnew` ワーカー、デッドライン、キャンセル |
| ライブラリ検索、カスタムシンボル/フットプリント作成・ウィザード | | ✓ ピン/パッド/3D モデルの整合性検証 |
| パネライゼーション（面付け） | | ✓ マウスバイトおよび V カットによる別基板出力 |
| Altium / Eagle 等の外部基板インポート | ✓ 基板変換 | ✓ 出力先指定、リードバック検証、元ファイル保全レポート |
| KiCad IPC API を介したライブ GUI 操作 | | ✓ |
| SPICE シミュレーション（`ngspice`） | | ✓ |
| AI エージェントスキル（`kce skill install`） | | ✓ |

---

## 動作要件とインストール

* **KiCad 10.x**（`kicad-cli` と同梱の `pcbnew` Python モジュール）。
* **Python 3.10+**（`kce` 自体の実行用。`pcbnew` は自動検出された KiCad 自身のインタープリタで動作します）。
* *注：* PLY 3D 出力には OCCT 7.7 以降でビルドされた KiCad が必要です（古いビルドでも STEP 出力は可能です）。

| OS | KiCad 10 のインストール方法 |
|---|---|
| **Windows** | [公式インストーラー](https://www.kicad.org/download/windows/)、`choco install kicad`、または `winget install KiCad.KiCad` |
| **macOS** | `brew install --cask kicad`（または `.dmg`） |
| **Ubuntu / Debian** | `sudo add-apt-repository ppa:kicad/kicad-10.0-releases && sudo apt install kicad` |
| **Fedora / Arch** | `sudo dnf install kicad` / `sudo pacman -S kicad kicad-library` |
| **Flatpak** | `flatpak install flathub org.kicad.KiCad`（自動検出に対応） |

公開リポジトリから `kce` をインストールします：

```bash
uv tool install git+https://github.com/Hybirdss/kicad-cli-extended      # 推奨
# または
pipx install git+https://github.com/Hybirdss/kicad-cli-extended
```

環境を確認し、初回のみデフォルトのライブラリテーブルを初期化します：

```bash
kce --json info doctor          # kicad-cli、pcbnew インタープリタ、ライブラリパスを確認 ("ok": true)
kce lib init-global-tables      # 初回のみ：KiCad のデフォルトグローバルライブラリテーブルを作成
```

---

## 最初のLED基板を作る

2ピンコネクタから給電し、抵抗で電流制限するLED基板（3.3 V電源、330 Ω抵抗）を一から作成する例です。空のディレクトリで実行してください：

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

**検証ポイント：**
* **部品 3 個、2ピンネット 3 本**、ERC/DRC エラー 0 件、未接続やパリティ不一致が 0 件であること。
* `pcb score` でコートヤード重複が 0 件、基板外形からはみ出した部品が 0 件であること。
* `fab package` が `complete: true`、空の `failed_steps`、および `outputs/fab-jlcpcb/` 配下の製造ファイル一覧を返すこと。

---

## ECO（設計変更）のレビューと製造パッケージのリリース

ネット `VCC` 上の既存 `U2` の近くにパスコン `C10` を追加する回路図 ECO が検証済みの場合、PCB のチェックポイントを作成し、同期をプレビューした上で、配置と配線を対象部品に限定して実行します：

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

* **同期の安全性：** 適用前にドライラン結果の `replaced[].pads_moved`、`unmatched_pins`、`tracks_ending_on_old_pads` を確認します。
* **スコープ付きゲート：** `project compare --fail-on-unexpected` は、`--allow` リスト外の変更を検出すると終了コード `4` で拒否します。
* **実装用パッケージのリリース：** リリース前に `fab parts check --offline` と `fab bom-check --require MPN,LCSC,Footprint` を実行し、`fab package --preset jlcpcb --require-lcsc --require-rev v1.3.0 --manifest --name-suffix "{rev}-{preset}" --fail-on-drc -o RELEASE` でパッケージ化します。

---

## AIエージェントスキル

`kce` には、上記のワークフローと検証ゲートをAIコーディングエージェントに教えるスキル（`SKILL.md` + `COMMANDS.md`）が同梱されています。

```bash
kce skill install                                 # 検出されたすべてのエージェント + ~/.agents/skills にインストール
kce skill install --target claude --target codex --dry-run
kce skill install --dir ./.claude/skills          # プロジェクトローカルにインストール
kce skill status
```

---

## ワークフロー

[インストールされるスキル](https://github.com/Hybirdss/kicad-cli-extended/blob/main/skills/kicad-cli-extended/SKILL.md#choose-a-scenario-recipe)には12個のトリガー駆動レシピが含まれており、詳細は [`docs/AGENT-RECIPES.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/AGENT-RECIPES.md) に記載されています。

| 目的 | 主なコマンドチェーン | セクション |
|---|---|---|
| 新規基板の設計と製造ファイル出力 | `project new` → `sch apply` → `pcb update` → `fab package` | [最初の基板設計と製造](#最初の基板設計と製造) |
| ネットリスト JSON 仕様からの段階的設計 | `session checkpoint --immutable` → `run stage.json` | [仕様からのネットリスト主導設計](#仕様からのネットリスト主導設計) |
| スコープを絞った ECO の適用と検証 | `pcb update --keep-extra --lock-existing` → `project compare` | [既存プロジェクトの設計変更（ECO）](#既存プロジェクトの設計変更eco) |
| CI での PR ゲートとハードウェアリリース | `check erc` → `check drc` → `fab bom-check` → `fab package` | [継続的インテグレーション（CI）とリリース](#継続的インテグレーションciとリリース) |
| ウォーム `pcbnew` ワーカーによる配置探索 | `serve` → `run trial.json` → `session restore best` | [ウォームワーカーによる配置探索](#ウォームワーカーによる配置探索) |
| 不足しているシンボル/フットプリントの作成と検証 | `lib sym new` → `lib fp wizard` → `lib link-check` → `lib fp check` | [ライブラリにない部品の追加](#ライブラリにない部品の追加) |
| 外部基板インポート、SPICE、高速配線、面付け | `project import` / `sim` / `pcb diff-pair` / `pcb panelize` | [外部基板、シミュレーション、高速配線、面付け](#外部基板シミュレーション高速配線面付け) |

### 最初の基板設計と製造

上記の LED クイックスタートに従い、表面実装（SMT）を発注する場合は確認済みのサプライヤー部品番号を設定します。コネクタの基板端配置は `pcb move` と `pcb lock` で行い、製造上の最小ルールは `project rules` と `project netclass` で設定します。

### 仕様からのネットリスト主導設計

承認された電気的インテントを JSON としてプロジェクト内に保持し、各段階を受け入れ条件付きのアトミックな `run` プランとして実行し、直後にイミュータブル（`--immutable`）なチェックポイントを作成します：

```bash
kce --json -p sensor session checkpoint initial --immutable
kce --json -p sensor --dry-run run schematic-stage.json
kce --json -p sensor run schematic-stage.json
kce --json -p sensor session checkpoint schematic-ok --immutable
kce --json -p sensor run board-stage.json
```

### 既存プロジェクトの設計変更（ECO）

ベースラインのネット、フットプリント、スコア、ERC/DRC を記録し、`pre-eco` チェックポイントを作成します。回路図プランと厳密なインテント検証（`sch verify --subset --strict`）を経てから PCB を同期し、`project compare checkpoint:pre-eco --geometry --allow ...` で意図しない変更がないことを保証します。

### 継続的インテグレーション（CI）とリリース

[PR ワークフローテンプレート](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-ci.yml)と[ハードウェアリリーステンプレート](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-release.yml)を使用して、ツールのバージョン固定、ベースリビジョンの取得（`fetch-depth: 0`）、ERC/DRC ゲート、BOM 価格/フィールドチェックを自動化します。

### ウォームワーカーによる配置探索

`baseline`、`best-000000`（`--immutable`）、および `best` チェックポイントを作成し、`kce serve` を起動したまま試行プランを繰り返します。重複ゼロ・外形逸脱ゼロと DRC パスを満たした候補のみを `best` として更新します。

### ライブラリにない部品の追加

`lib sym search` / `lib fp search` で候補を探し、存在しない場合は `lib sym new`、`lib fp new`、`lib fp wizard` でプロジェクトローカルライブラリに作成します。配置前に必ず `lib link-check` と `lib fp check` を実行してデータシートと照合します。

### 外部基板、シミュレーション、高速配線、面付け

* **外部基板インポート：** Altium / Eagle 基板は新しいディレクトリにインポートし、`read_back` と変換ロスを確認します（姉妹ツール [`ace`](https://github.com/Hybirdss/altium-cli-extended) で元の Altium ファイルを直接検査できます）。
* **SPICE シミュレーションと高速配線：** `sim` でフィルタや回路ブロックを検証し、高速信号は「ルール設定 → 差動ペア配線 → 短辺ミアンダ調整 → ビアフェンス/ステッチ → ティアドロップ → ゾーン再塗りつぶし → DRC → 配線長レポート」の順に実行します。面付けは最後に `pcb panelize` で別ファイルに出力します。

---

## コマンドグループ

| グループ | 機能概要 |
|---|---|
| `project` | テンプレートからの新規作成、外部基板インポート、情報/ファイル一覧、テキスト変数、ネットクラス、デザインルール、ライブラリテーブル、バリアント、アーカイブ、リビジョン比較 |
| `sch` | シンボル配置/移動/回転、フィールド編集、配線、ラベル、電源、未接続フラグ、階層シート、アノテーション、ネット一覧、インテント `apply`/`verify`；`kicad-cli` `erc`、`export *`、`upgrade` |
| `pcb` | 基板検査、回路図からの更新、配置スコア、配置補正、近傍配置、配線、自動配線、差動ペア、等長調整、ビアステッチ/フェンス、ティアドロップ、ゾーン/キープアウト、基板外形、レイヤー、3Dモデル、面付け、バーコード、穴、フィデューシャル、DSN/SES；`kicad-cli` `drc`、`render`、`export *`、`import`、`upgrade` |
| `lib` | シンボル/フットプリント検索・詳細表示、フットプリント提案、新規作成/ウィザード、ピン/パッド整合性チェック、ポータブル3Dモデル設定 |
| `check` | ERC、DRC（ゾーン再塗りつぶし＋回路図パリティ含む）、修正ヒント付き `diagnose` |
| `fab` | 製造パッケージ（`generic`、`jlcpcb`、`pcbway`、`oshpark`）、部品/BOM チェック、価格・リビジョンゲート、マニフェスト生成 |
| `jobset` | `.kicad_jobset` ファイルの作成と実行 |
| `preview` | `kicad-cli` による SVG/PNG プレビュー生成、リビジョン差分バンドル、ライブプレビュー |
| `gui` | KiCad IPC API による実行中 GUI の操作：ステータス、選択、アクション、保存/再読込/再塗りつぶし |
| `sim` | `ngspice` による SPICE シミュレーション：ディレクティブ、モデル、ネットリスト、実行 |
| `session` | ステータス、Undo/Redo/履歴、保存/破棄、チェックポイントの作成/一覧/復元/削除 |
| `run` | コミット前受け入れ条件付きのアトミックな JSON プラン実行 |
| `serve` | ウォーム `pcbnew` ワーカー、デッドライン、キャンセル機能を備えた stdio JSON-RPC 2.0 サーバー |
| `errors` | 安定したエラー型、カテゴリ、終了コード、復旧コマンドの一覧 |
| `raw` | 任意の KiCad ファイルに対する S式クエリ/編集、および `pcbnew` Python 実行 |
| `repl` | 対話型シェル（サブコマンド省略時のデフォルト） |
| `info` / `skill` | 環境診断（`doctor`）、機能確認、バージョン、コマンドメタデータ、オフライン JSON スキーマ / AI エージェントスキルのインストール |

---

## JSON出力契約

* **成功（終了コード `0`）：** `--json` を指定すると、`stdout` に1つの JSON ドキュメントを出力します。
* **失敗（終了コード `1`〜`7`）：** `stderr` に [`schemas/error.schema.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/error.schema.json) に準拠した1つの JSON エラーオブジェクトを出力します。
* **エラーカテゴリと終了コード：**

  | カテゴリ (`category`) | 終了コード | 意味 | 主な `type` 値 |
  |---|:-:|---|---|
  | `input` | 2 | 引数不正、未知のリファレンス/ピン、ファイル不在、不正な入力ファイル、ロックされた要素 | `UsageError`, `KicadInputError`, `KeyError`, `ValueError`, `SexprError`, `FileNotFoundError`, `FileExistsError`, `ItemLocked`, `RuntimeError` |
  | `design` | 3 | 設計ルール違反または意図しない接続変更のため拒否 | `NetMergeError`, `NetSplitError`, `RouteBlocked`, `DesignMismatch`, `AcceptanceFailed`, `PlacementBlocked`, `JobsetFailed`, `KicadCheckFailed`, `RuntimeError` (ERC/DRC 失敗) |
  | `validation` | 4 | 保存されたファイルを KiCad が拒否（変更はロールバック済み）、または比較ゲートが想定外の変更を検出 | `ValidationError` |
  | `crash` | 5 | `kicad-cli` または `pcbnew` ワーカーが異常終了 | `KicadCrash` |
  | `timeout` | 6 | `kicad-cli` が `KCE_TIMEOUT`（デフォルト 1800 秒）を超過、またはワーカーがタイムアウト | `KicadTimeout`, `ToolTimeout` |
  | `environment` | 7 | KiCad または外部ツールが見つからない、あるいは OS レベルの I/O エラー | `KicadNotFound`, `OSError`, `RuntimeError` |
  | `internal` | 1 | `kce` 内部のバグ（`details.traceback` にスタックトレースを格納） | `KicadError`, `RuntimeError`, `InternalError`, `TypeError`, `IndexError` |

---

## PowerShellとCI

PowerShell では引数配列を使用し、シェル固有のループの代わりに `run` プランを使用してください：

```powershell
$KCE = "--json", "-p", "led"
& kce @KCE info doctor
& kce @KCE session checkpoint before-eco --immutable
& kce @KCE --dry-run run eco.json
& kce @KCE run eco.json
# 負の値を含むカンマ区切り引数は '=' で結合します: --rotate=-45,0,45
```

CI 実行前に `.gitignore` と `.gitattributes` に以下を追加してください：

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

## 開発とテスト

```bash
git clone https://github.com/Hybirdss/kicad-cli-extended && cd kicad-cli-extended
python -m pip install -e ".[test]"
python scripts/fetch_fixtures.py               # E2Eテスト用のKiCadデモプロジェクトを取得
python -m pytest tests/test_core.py            # ユニットテスト（KiCad不要）
python scripts/gen_skill.py --check            # スキル/リファレンス/スキーマの同期チェック
KCE_FORCE_INSTALLED=1 python -m pytest tests   # 実KiCadを使用した完全テストスイート
```

詳細は [`docs/TESTING.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/TESTING.md) を参照してください。

---

## ライセンス

GPL-3.0-or-later。`kicad-cli-extended` は独立したオープンソースプロジェクトであり、KiCad プロジェクトと提携または承認されたものではありません。KiCad は KiCad プロジェクトの商標です。詳細は [LICENSE](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE) を参照してください。
