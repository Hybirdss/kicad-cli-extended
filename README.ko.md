<p align="center"><img src="https://raw.githubusercontent.com/Hybirdss/kicad-cli-extended/main/docs/assets/banner.png" alt="kicad-cli-extended" width="820"></p>

<div align="center">

# kicad-cli-extended (`kce`)

**AI 에이전트와 하드웨어 자동화를 위해 확장된 KiCad 공식 `kicad-cli`**

[![CI](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml/badge.svg)](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/pyproject.toml)

[English](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.md) | **한국어** | [简体中文](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.zh-CN.md) | [繁體中文](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.zh-TW.md) | [日本語](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.ja.md) | [Español](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.es.md) | [हिन्दी](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.hi.md)

[벤치마크](#벤치마크) · [기능 비교](#kicad-cli-vs-kicad-cli-extended) · [설치](#요구사항-및-설치) · [빠른 시작](#첫-번째-led-보드-만들기) · [에이전트 스킬](#ai-에이전트-스킬) · [워크플로](#워크플로) · [명령어 그룹](#명령어-그룹) · [JSON 규약](#json-출력-규약) · [CI 및 PowerShell](#powershell-및-ci)

</div>

> **Altium Designer를 사용하시나요?** 자매 도구인 **[altium-cli-extended (`ace`)](https://github.com/Hybirdss/altium-cli-extended)**를 확인해 보세요. 두 프로젝트 모두 GPL-3.0-or-later 라이선스를 따릅니다.

**`kce`** (KiCad CLI Extended)는 KiCad 공식 `kicad-cli`의 모든 명령어와 옵션을 100% 그대로 지원하면서, 회로도 편집, 넷리스트 기반 회로 구성(`sch apply`/`verify`), PCB 동기화, 배치 품질 평가(`pcb score`), 배선/자동 배선, 제조용 패키지(JLCPCB/PCBWay) 생성, 리비전 비교, 트랜잭션 실행 플랜, 그리고 AI 에이전트 스킬을 일관된 JSON 규약으로 제공합니다.

실제 **KiCad 10**(`kicad-cli` 및 내장 `pcbnew` Python 모듈)을 직접 구동하며, 파일을 저장할 때마다 `kicad-cli`로 다시 로드해 KiCad 엔진 자체가 변경 사항을 검증하도록 설계되었습니다.

---

## 벤치마크

`kce`는 상시 로드되는 스킬 프론트매터 토큰이 **157 토큰**(`SKILL.md` 전체 로드 시 3,737 토큰)에 불과합니다. 반면 2026-10-28 기준 GitHub 스타 수 상위 2개 공개 KiCad MCP 서버는 도구 정의만으로도 **4,927~63,420 토큰**을 소비합니다. 전체 명령어 레퍼런스(`COMMANDS.md`)는 필요할 때만 열람하며 11,238 토큰입니다.

2026-10-30 기준 16-CPU Linux 머신에서 KiCad 10.0.7, Python 3.11.17, `tiktoken` 0.14.0 (`o200k_base`)으로 측정했습니다. KiCad 기본 데모인 `pic_programmer`의 깨끗한 복사본에서 5회 교차 반복 실행했으며, 모든 반복은 시스템 부하 4.00 미만(기록된 부하: 2.02, 3.38, 3.54, 3.45, 3.36)에서 시작했습니다. 토큰은 요청+응답 중앙값, 시간은 실제 소요 시간(wall clock) 중앙값입니다. 자세한 내용은 [측정 방법](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/METHOD.md), [요약 및 범위](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/SUMMARY-2026-10-30.md), [전체 실행 기록](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/results-2026-10-30.json)을 참조하세요.

| 방식 | 노출 도구 수 | 작업 시작 전 컨텍스트 토큰 |
|---|---:|---:|
| **`kce` (스킬 프론트매터)** | 명령어 1개 | **157** |
| `kce` (`SKILL.md` 전체, 프론트매터 포함) | 명령어 1개 | 3,737 |
| `mixelpixx/KiCAD-MCP-Server` 2.8.3 (선정 당시 2,995★), 전체 도구 | 244 | **63,420** |
| `mixelpixx`, `core-toolbox` 설정 | 37 | **6,445** |
| `lamaalrajih/kicad-mcp` (선정 당시 531★) | 16 | **4,927** |

| 작업 (`pic_programmer` 데모 기준) | `kce` (토큰 / 시간) | `mixelpixx` (토큰 / 시간) | `lamaalrajih` (토큰 / 시간) |
|---|---|---|---|
| ERC 요약 | 108 / 3.62 s | 69 / 3.09 s | 도구 없음 |
| DRC | 119 / 4.45 s | 278 / 3.63 s | 109 / 3.40 s |
| BOM 추출 | 177 / 1.00 s | 111 / 0.59 s | 133 / 0.53 s |
| Gerber + 드릴 출력 | 2,771 / 2.15 s | 645 / 1.43 s | 도구 없음 |

지원되는 50회 실행 모두 디스크에 정상 출력 파일(BOM, Gerber, 드릴)을 생성하거나 정확한 ERC/DRC 위반 수를 반환했습니다.

---

## `kicad-cli` vs. `kicad-cli-extended`

KiCad 10.0.7 `kicad-cli`의 `--help` 트리(43개 명령어, 582개 옵션; [`kicad_cli_spec.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/src/kicad_cli_extended/utils/kicad_cli_spec.json)에 수록)를 기반으로 비교한 표입니다.

| 기능 | `kicad-cli` | `kicad-cli-extended` (`kce`) |
|---|:-:|:-:|
| 내보내기 및 검사 (ERC, DRC, Gerber, 드릴, BOM, STEP, PDF, 렌더링 등) | ✓ | ✓ 43개 모든 명령어 및 모든 옵션 패스스루 지원 |
| 모든 명령어의 구조화된 JSON 출력 | `erc`, `drc`, `export stats`, `import` 리포트 파일만 지원 | ✓ 일관된 JSON 규약 및 에러 카테고리 |
| 회로도 편집: 부품 배치, 와이어 배선, 라벨, 자동 번호 부여(Annotate) | | ✓ |
| 넷리스트 우선 회로 구성 (`sch apply` / `sch verify`) | 넷리스트 내보내기만 가능 | ✓ Intent JSON 적용, 부분 패치 및 식별자 검증 |
| 회로도에서 PCB 업데이트 (`pcb update`) | `drc --schematic-parity` 검사만 가능 | ✓ 고정 UUID 유지, 패드 이동 및 기존 트랙 검사 |
| 배치 품질 점수 산출 및 범위 제한 자동 보정 | | ✓ `score`, `legalize`, `place near` |
| 배선, 자동 배선(Autoroute), 구리 영역(Zone), 보드 외곽선 | `drc` / 플롯 시 Zone 리필만 지원 | ✓ |
| 차동 쌍(Diff pair), 길이/스큐 규칙 및 튜닝 | DRC 검사만 가능 | ✓ 규칙 설정, 결합 배선, 미앤더(Meander) 튜닝 |
| 비아 펜스/스티칭, 티어드롭(Teardrop), 규칙 영역 | | ✓ |
| 넷 클래스 및 설계 규칙(Design Rules) | 읽기 전용 | ✓ 읽기 및 쓰기 |
| 제조용 패키지 (JLCPCB, PCBWay, OSH Park) | Jobset만 지원, 제조사 프리셋 없음 | ✓ 스테이징 기반 원자적 패키지 생성 및 BOM/CPL 게이트 |
| 프리뷰 및 리비전 비교 (`project compare`) | 렌더링 및 플롯만 가능 | ✓ 기하 구조 diff, 허용 목록(`--allow`), 시각적 번들 |
| 원자적 실행 플랜 (`run`) 및 이름 지정 체크포인트 (`session`) | | ✓ 커밋 전 수용 조건(Predicate) 검증 및 복원 |
| 상주형 JSON-RPC 워커 (`serve`) | | ✓ 웜 `pcbnew` 워커, 실행 제한시간, 작업 취소 |
| 라이브러리 검색, 커스텀 심볼/풋프린트 제작 및 위저드 | | ✓ 핀/패드/3D 모델 검증 |
| 패널라이제이션 (Panelization) | | ✓ 마우스바이트(Mouse bites) 및 V-컷 별도 보드 생성 |
| Altium / Eagle 등 외부 보드 가져오기 | ✓ 보드 변환 | ✓ 대상 지정, 리드백(Read-back) 및 원본 보존 리포트 |
| KiCad IPC API를 통한 실시간 GUI 제어 | | ✓ |
| SPICE 시뮬레이션 (`ngspice`) | | ✓ |
| AI 에이전트 스킬 (`kce skill install`) | | ✓ |

---

## 요구사항 및 설치

* **KiCad 10.x** (`kicad-cli` 및 내장 `pcbnew` Python 모듈 포함).
* **Python 3.10+** (`kce` 실행용). `pcbnew`는 자동으로 감지된 KiCad 자체 인터프리터에서 실행됩니다.
* *참고:* PLY 3D 내보내기는 OCCT 7.7 이상으로 빌드된 KiCad가 필요합니다(구버전에서도 STEP 내보내기는 가능).

| 운영체제 | KiCad 10 설치 방법 |
|---|---|
| **Windows** | [공식 설치 프로그램](https://www.kicad.org/download/windows/), `choco install kicad`, 또는 `winget install KiCad.KiCad` |
| **macOS** | `brew install --cask kicad` (또는 `.dmg` 설치) |
| **Ubuntu / Debian** | `sudo add-apt-repository ppa:kicad/kicad-10.0-releases && sudo apt install kicad` |
| **Fedora / Arch** | `sudo dnf install kicad` / `sudo pacman -S kicad kicad-library` |
| **Flatpak** | `flatpak install flathub org.kicad.KiCad` (자동 감지됨) |

공개 저장소에서 `kce`를 설치합니다:

```bash
uv tool install git+https://github.com/Hybirdss/kicad-cli-extended      # 권장
# 또는
pipx install git+https://github.com/Hybirdss/kicad-cli-extended
```

설치 환경을 확인하고 기본 라이브러리 테이블을 초기화합니다(최초 1회):

```bash
kce --json info doctor          # kicad-cli, pcbnew 인터프리터, 라이브러리 경로 확인 ("ok": true)
kce lib init-global-tables      # 최초 실행 시 1회: KiCad 기본 라이브러리 테이블 초기화
```

---

## 첫 번째 LED 보드 만들기

2핀 커넥터로 전원을 공급받아 저항으로 전류를 제한하는 LED 보드(3.3 V 전원, 330 Ω 저항)를 처음부터 끝까지 만드는 예제입니다. 빈 디렉터리에서 시작하세요:

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

**확인 포인트:**
* **부품 3개, 2핀 넷 3개**, ERC/DRC 오류 0건, 미연결(unconnected) 및 패리티 불일치 0건이어야 합니다.
* `pcb score`에서 코트야드(courtyard) 중첩 0건, 보드 외곽선 이탈 부품 0건을 확인합니다.
* `fab package` 결과에서 `complete: true`, 빈 `failed_steps`, 그리고 `outputs/fab-jlcpcb/` 아래 생성된 파일 목록을 확인합니다.
* *제조 참고:* 이 예제는 베어보드(Bare-board) 기준입니다. SMT 실장 주문 시에는 공급사 부품 번호(LCSC 등)를 지정하고 제조사 뷰어에서 CPL 배치 방향을 반드시 확인하세요. 3D 프리뷰가 필요하면 `info doctor --require render` 확인 후 `pcb render`를 사용하세요.

> **대화형 셸 및 명령어 탐색:** 인자 없이 `kce`만 실행하면 대화형 REPL 셸이 열립니다. `kce --help`, `kce <group> --help`, `kce --json info commands --full`, `kce --json info describe pcb score`로 전체 명령어 트리를 조회할 수 있습니다.

---

## ECO 검토 및 제조 패키지 릴리스

`VCC` 넷의 기존 `U2` 근처에 신규 커패시터 `C10`을 추가하는 회로도 ECO가 검증되었다면, PCB 체크포인트를 만든 뒤 동기화 프리뷰 및 범위 제한(Scoped) 배치·배선을 수행합니다:

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

* **동기화 안전성:** 실제 적용 전 드라이런 결과에서 `replaced[].pads_moved`, `unmatched_pins`, `tracks_ending_on_old_pads`를 확인하세요. 패드 위치가 바뀌는 풋프린트 교체가 승인된 경우 `--lock-existing`을 생략해야 합니다.
* **범위 제한 게이트:** 범위 지정 작업은 다른 풋프린트의 저장된 잠금 플래그를 건드리지 않고 위치를 고정합니다. `project compare --fail-on-unexpected`는 `--allow` 목록 밖의 변경이 감지되면 종료 코드 `4`로 거부합니다.
* **조립용 패키지 릴리스:** 출시 전 `fab parts check --offline` 및 `fab bom-check --require MPN,LCSC,Footprint`를 실행하고, `fab package --preset jlcpcb --require-lcsc --require-rev v1.3.0 --manifest --name-suffix "{rev}-{preset}" --fail-on-drc -o RELEASE`로 패키지를 생성하세요. 모든 출력은 스테이징에서 생성되며 `complete: true`일 때만 게시됩니다.

---

## AI 에이전트 스킬

`kce`는 에이전트에게 위 워크플로와 검증 게이트를 학습시키는 내장 스킬(`SKILL.md` + `COMMANDS.md`)을 포함하고 있습니다.

```bash
kce skill install                                 # 현재 머신에서 감지된 모든 에이전트 + ~/.agents/skills에 설치
kce skill install --target claude --target codex --dry-run
kce skill install --dir ./.claude/skills          # 프로젝트 로컬 설치
kce skill status
```

지원 타겟: `agents` (`~/.agents/skills`), `claude` (`~/.claude/skills`), `codex` (`~/.codex/skills`), `gemini` (`~/.gemini/skills`) 또는 `--dir`을 통한 임의 디렉터리. 심볼릭 링크 대신 파일을 복사하므로 Windows, macOS, Linux 모두 동일하게 작동합니다.

---

## 워크플로

[설치된 스킬](https://github.com/Hybirdss/kicad-cli-extended/blob/main/skills/kicad-cli-extended/SKILL.md#choose-a-scenario-recipe)에는 12가지 트리거 기반 레시피가 포함되어 있으며, 상세 내용은 [`docs/AGENT-RECIPES.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/AGENT-RECIPES.md)에 정리되어 있습니다. ERC와 DRC는 기본적으로 리포트 전용이므로 항상 명시적인 게이트 옵션(`--fail-on-error`, `--fail-on-drc`, `--strict`)을 함께 사용하세요.

| 목표 | 핵심 명령어 체인 | 섹션 |
|---|---|---|
| 새 보드 설계 및 제조 파일 생성 | `project new` → `sch apply` → `pcb update` → `fab package` | [첫 보드 제작 및 제조](#첫-보드-제작-및-제조) |
| 넷리스트 JSON 명세 기반 다단계 설계 | `session checkpoint --immutable` → `run stage.json` | [명세 기반 넷리스트 우선 보드 설계](#명세-기반-넷리스트-우선-보드-설계) |
| 범위 제한 ECO 적용 및 검증 | `pcb update --keep-extra --lock-existing` → `project compare` | [기존 프로젝트의 엔지니어링 변경(ECO)](#기존-프로젝트의-엔지니어링-변경eco) |
| CI에서 PR 검사 및 하드웨어 릴리스 | `check erc` → `check drc` → `fab bom-check` → `fab package` | [지속적 통합(CI) 및 태그 릴리스](#지속적-통합ci-및-태그-릴리스) |
| 웜 `pcbnew` 워커를 활용한 배치 탐색 | `serve` → `run trial.json` → `session restore best` | [웜 워커를 활용한 배치 최적화 탐색](#웜-워커를-활용한-배치-최적화-탐색) |
| 누락된 심볼/풋프린트 제작 및 검증 | `lib sym new` → `lib fp wizard` → `lib link-check` → `lib fp check` | [라이브러리에 없는 부품 추가](#라이브러리에-없는-부품-추가) |
| 외부 보드 가져오기, SPICE, 고속 배선, 패널화 | `project import` / `sim` / `pcb diff-pair` / `pcb panelize` | [외부 보드 가져오기, 시뮬레이션, 고속 배선 및 패널화](#외부-보드-가져오기-시뮬레이션-고속-배선-및-패널화) |

### 첫 보드 제작 및 제조

위 LED 빠른 시작을 따라 기본 흐름을 익힌 뒤, 실제 조립용 보드에서는 검증된 부품 번호를 지정하세요. USB-C 설계 시에는 인텐트를 작성하기 전에 커넥터의 실제 핀맵과 전력 역할을 먼저 확인합니다. 부품 검색/조회 → 체크포인트 → `sch apply` 드라이런 및 실행/검증/ERC → 외곽선 설정 → `pcb update` 동기화 → 커넥터 수동 배치(`pcb move`, `pcb lock`) → 제조 규칙 설정(`project rules`/`netclass`) → 배치 점수 확인, 배선, 구리 영역 채우기, DRC 게이트 → `fab package` 순으로 진행합니다.

### 명세 기반 넷리스트 우선 보드 설계

승인된 전기적 인텐트(Intent)를 JSON으로 프로젝트 내부에 보관하고(초기 체크포인트에 포함), 실행 플랜과 저널은 프로젝트 외부에 둡니다. 각 단계는 수용 조건이 포함된 원자적 `run` 플랜으로 실행하고 직후에 불변(`--immutable`) 체크포인트를 남깁니다:

```bash
kce --json -p sensor session checkpoint initial --immutable
kce --json -p sensor --dry-run run schematic-stage.json
kce --json -p sensor run schematic-stage.json
kce --json -p sensor session checkpoint schematic-ok --immutable
kce --json -p sensor run board-stage.json
```

### 기존 프로젝트의 엔지니어링 변경(ECO)

베이스라인 넷, 풋프린트/패드, 배치 점수, ERC/DRC를 기록하고 `pre-eco` 체크포인트를 생성합니다. 회로도 플랜을 실행하고 엄격한 인텐트 검증(`sch verify --subset --strict`)을 통과한 후 PCB를 동기화합니다. `project compare checkpoint:pre-eco` (또는 `git:HEAD`)에 `--geometry`와 `--allow` 목록을 지정해 허용된 범위 외의 변경이 없는지 검증합니다.

### 지속적 통합(CI) 및 태그 릴리스

[PR 워크플로 템플릿](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-ci.yml)과 [하드웨어 릴리스 워크플로 템플릿](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-release.yml)을 활용하세요. 도구 버전 고정, 베이스 리비전 fetch(`fetch-depth: 0`), ERC/DRC 임계치 게이트, 리비전 비교, BOM 필드/가격 검증, 타이틀 블록 리비전 일치 여부를 모두 자동으로 검사할 수 있습니다.

### 웜 워커를 활용한 배치 최적화 탐색

`baseline`, `best-000000`(`--immutable`), 그리고 `best` 체크포인트를 만든 뒤 포그라운드 `kce serve` 프로세스를 띄웁니다. 각 후보 배치를 트라이얼 플랜으로 실행하며 `expect` 조건으로 코트야드 중첩 0건 및 외곽선 이탈 0건을 검증합니다. DRC까지 통과한 후보만 새로운 불변 세대로 저장하고 `best`를 갱신하며, 실패 시 `best`로 복원합니다.

### 라이브러리에 없는 부품 추가

정규화된 이름과 노출 패드(Exposed Pad) 변형을 먼저 검색(`lib sym search`, `lib fp search`)한 뒤, 없을 경우 프로젝트 로컬 라이브러리에 `lib sym new`, `lib fp new`, `lib fp wizard`로 생성합니다. 배치 전 반드시 `lib link-check`와 `lib fp check`를 실행해 핀 테이블과 패드 치수를 데이터시트와 대조하세요.

### 외부 보드 가져오기, 시뮬레이션, 고속 배선 및 패널화

* **외부 보드 가져오기:** Altium/Eagle 보드는 새 디렉터리로 가져오고(`project import` 또는 `pcb import`), 원본 보존 리포트와 `read_back` 결과를 점검합니다(자매 도구 [`ace`](https://github.com/Hybirdss/altium-cli-extended)로 네이티브 Altium 파일을 직접 분석할 수 있습니다).
* **SPICE 시뮬레이션:** 스크래치 프로젝트에서 `sim`으로 먼저 검증하고 노드 `0`, AC 자극, 프로브 이름, 경고를 확인합니다.
* **고속 배선 및 패널화:** 규칙 설정 → 차동 쌍 배선 → 짧은 라인 미앤더 튜닝 → 비아 펜스/스티칭 → 티어드롭 → Zone 리필 → DRC → 길이 리포트 순으로 진행하며, 패널화(`pcb panelize`)는 마지막에 별도 보드 파일로 수행합니다.

---

## 명령어 그룹

| 그룹 | 설명 |
|---|---|
| `project` | 템플릿 기반 프로젝트 생성, 외부 보드 가져오기, 정보/파일 조회, 텍스트 변수, 넷 클래스, 설계 규칙, 라이브러리 테이블, 변형(Variant), 아카이브, 리비전 비교 |
| `sch` | 심볼 배치/이동/회전, 필드, 와이어, 라벨, 전원, 미연결(no-connect), 계층 시트, 번호 부여, 넷 조회, 인텐트 `apply`/`verify`; `kicad-cli` `erc`, `export *`, `upgrade` |
| `pcb` | 보드 조회, 회로도 동기화, 배치 점수(`score`), 보정(`legalize`), 인접 배치(`place near`), 배선, 자동 배선, 차동 쌍, 길이 튜닝, 비아 스티칭/펜스, 티어드롭, Zone/Keepout, 외곽선, 레이어, 3D 모델, 패널화, 바코드, 홀, 피듀셜, DSN/SES; `kicad-cli` `drc`, `render`, `export *`, `import`, `upgrade` |
| `lib` | 심볼/풋프린트 검색 및 상세 조회, 풋프린트 추천, 신규 생성/위저드, 핀/패드 정합성 검사, 이식 가능한 3D 모델 설정 |
| `check` | ERC, DRC(Zone 리필 + 회로도 패리티 포함), 수정 힌트를 제공하는 `diagnose` |
| `fab` | 제조 패키지(`generic`, `jlcpcb`, `pcbway`, `oshpark`), 부품/BOM 검사, 가격 및 리비전 게이트, 매니페스트 생성 |
| `jobset` | `.kicad_jobset` 파일 생성 및 실행 |
| `preview` | `kicad-cli` 기반 SVG/PNG 프리뷰 렌더링, 리비전 diff 번들, 라이브 프리뷰 세션 |
| `gui` | 실행 중인 KiCad GUI를 IPC API로 제어: 상태 조회, 선택 영역, 액션 실행, 저장/리로드/리필 |
| `sim` | `ngspice` 기반 SPICE 시뮬레이션: 디렉티브, 모델, 넷리스트, 시뮬레이션 실행 |
| `session` | 세션 상태, 실행 취소/다시 실행/히스토리, 저장/취소, 체크포인트 생성/목록/복원/삭제 |
| `run` | 커밋 전 수용 조건(Predicate)을 포함한 원자적 다단계 JSON 플랜 실행 |
| `serve` | 웜 `pcbnew` 워커, 제한시간, 취소 기능을 갖춘 표준 입출력(stdio) JSON-RPC 2.0 상주 서버 |
| `errors` | 고정 에러 타입, 카테고리, 종료 코드, 복구 명령어 카탈로그 조회 |
| `raw` | 모든 KiCad 파일의 S-expression 직접 쿼리/편집 및 `pcbnew` Python 실행 |
| `repl` | 대화형 셸 (하위 명령어 생략 시 기본 실행) |
| `info` / `skill` | 환경 진단(`doctor`), 기능 확인, 버전, 명령어 메타데이터 및 오프라인 스키마 조회 / AI 에이전트 스킬 설치 |

### 넷리스트 인텐트: `sch apply` 및 `sch verify`

도면 좌표 대신 연결 관계(넷리스트)로 회로를 정의합니다([스키마](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/netlist-intent.schema.json)):

```bash
kce sch apply intent.json            # 심볼 배치, 필드 설정, 모든 넷 라벨링
kce sch verify --expect intent.json  # {"match": true, ...}
```

핀 집합을 기준으로 넷을 매칭하므로 KiCad의 자동 생성 넷 이름은 오류가 아닌 `renamed`로 보고됩니다. `sch apply`는 멱등성(idempotent)을 보장하여 같은 파일을 두 번 적용해도 바이트가 바뀌지 않으며, `--power-flags`와 `--prune`, 소규모 ECO를 위한 `--patch` 및 `sch verify --subset --strict`를 지원합니다. 전체 명령어 레퍼런스는 [`skills/kicad-cli-extended/COMMANDS.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/skills/kicad-cli-extended/COMMANDS.md), 내부 설계는 [`docs/ARCHITECTURE.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/ARCHITECTURE.md)를 참조하세요.

---

## 실행 플랜 및 상주형 세션

* **트랜잭션 플랜 (`kce run`):** `kce --json run plan.json`은 여러 단계를 단일 세션에서 실행하고 마지막 저장 시 KiCad로 파일을 검증합니다. 중간 단계나 검증이 실패하면 모든 프로젝트 파일 바이트와 히스토리를 완벽히 롤백합니다([플랜 스키마](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/plan.schema.json)).
* **이름 지정 체크포인트 (`kce session`):** `kce session checkpoint NAME`은 `outputs/`와 상태 폴더를 제외한 프로젝트 파일을 스냅샷으로 저장하며, `kce session restore NAME`은 파일 추가/삭제를 포함해 안전하게 복원하고 검증합니다.
* **상주형 JSON-RPC 서버 (`kce serve`):** 표준 입력 줄당 하나의 JSON-RPC 2.0 요청을 처리하며 보드 요청 간에 웜 `pcbnew` 워커를 재사용합니다.

---

## JSON 출력 규약

* **성공 (`0`):** `--json` 옵션을 주면 `stdout`에 단일 JSON 문서를 출력합니다.
* **실패 (`1`–`7`):** `stderr`에 [`schemas/error.schema.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/error.schema.json)을 따르는 단일 JSON 에러 객체를 출력합니다.
* **에러 카테고리 및 종료 코드:**

  | 카테고리 (`category`) | 종료 코드 | 의미 | 주요 `type` 값 |
  |---|:-:|---|---|
  | `input` | 2 | 잘못된 인자, 존재하지 않는 레퍼런스/핀, 파일 누락, 손상된 입력 파일, 잠긴 항목 | `UsageError`, `KicadInputError`, `KeyError`, `ValueError`, `SexprError`, `FileNotFoundError`, `FileExistsError`, `ItemLocked`, `RuntimeError` |
  | `design` | 3 | 설계 규칙 위반 또는 의도하지 않은 회로 변경으로 인해 거부됨 | `NetMergeError`, `NetSplitError`, `RouteBlocked`, `DesignMismatch`, `AcceptanceFailed`, `PlacementBlocked`, `JobsetFailed`, `KicadCheckFailed`, `RuntimeError` (ERC/DRC 실패) |
  | `validation` | 4 | 저장된 파일을 KiCad가 거부함(변경 사항 자동 롤백) 또는 비교 게이트에서 허용되지 않은 변경 감지 | `ValidationError` |
  | `crash` | 5 | `kicad-cli` 또는 `pcbnew` 워커 비정상 종료 | `KicadCrash` |
  | `timeout` | 6 | `kicad-cli`가 `KCE_TIMEOUT`(기본 1800초)을 초과하거나 워커가 제한시간을 초과함 | `KicadTimeout`, `ToolTimeout` |
  | `environment` | 7 | KiCad 또는 외부 도구 미설치, OS 수준 I/O 오류 | `KicadNotFound`, `OSError`, `RuntimeError` |
  | `internal` | 1 | `kce` 내부 버그 (`details.traceback`에 스택 포함) | `KicadError`, `RuntimeError`, `InternalError`, `TypeError`, `IndexError` |

---

## 환경 변수 설정

| 변수명 | 용도 |
|---|---|
| `KCE_KICAD_CLI` (별칭 `KICAD_CLI`) | `kicad-cli` 실행 파일 경로 |
| `KCE_PYTHON` | `import pcbnew`가 가능한 Python 인터프리터 경로 |
| `KCE_SHARE_DIR` | KiCad 데이터 디렉터리 (`share/kicad`) |
| `KICAD10_SYMBOL_DIR`, `KICAD10_FOOTPRINT_DIR`, `KICAD10_3DMODEL_DIR`, `KICAD10_TEMPLATE_DIR` | KiCad 라이브러리 경로 변수 |
| `KICAD_CONFIG_HOME` | KiCad 설정 디렉터리 |
| `KCE_NO_VALIDATE=1` | 파일 저장 후 `kicad-cli` 재로드 검증 생략 |
| `KCE_LOCK_TIMEOUT` | 동일 프로젝트를 편집 중인 다른 `kce` 프로세스 대기 시간(초, 기본 `120`) |
| `KCE_TIMEOUT` | 단일 `kicad-cli` 호출 제한시간(초, 기본 `1800`) |
| `KCE_SESSION_DIR` | 프로젝트별 히스토리/체크포인트를 저장할 외부 기본 디렉터리 (`--session-dir`로 재정의 가능) |
| `KCE_NGSPICE` / `KCE_LIBNGSPICE` | `ngspice` 실행 파일 또는 공유 라이브러리 경로 |
| `FREEROUTING_JAR`, `JAVA` | 선택적 Freerouting 자동 배선기 JAR 및 Java 실행 파일 |

---

## PowerShell 및 CI

PowerShell에서는 공통 옵션을 배열로 전달하고, 셸 루프 대신 `run` 플랜을 사용하세요:

```powershell
$KCE = "--json", "-p", "led"
& kce @KCE info doctor
& kce @KCE session checkpoint before-eco --immutable
& kce @KCE --dry-run run eco.json
& kce @KCE run eco.json
# 음수가 포함된 쉼표 구분 값은 '='로 연결합니다: --rotate=-45,0,45
```

[하드웨어 PR 워크플로](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-ci.yml) 및 [릴리스 아티팩트 워크플로](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-release.yml)를 `.github/workflows/`에 복사해 사용할 수 있습니다. CI 실행 전 `.gitignore`와 `.gitattributes`에 다음 규칙을 추가하세요:

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

## 개발 및 테스트

```bash
git clone https://github.com/Hybirdss/kicad-cli-extended && cd kicad-cli-extended
python -m pip install -e ".[test]"
python scripts/fetch_fixtures.py               # E2E 테스트용 KiCad 데모 프로젝트 다운로드
python -m pytest tests/test_core.py            # 유닛 테스트 (KiCad 미설치 환경에서도 실행 가능)
python scripts/gen_skill.py --check            # 스킬/레퍼런스/스키마 동기화 검사
KCE_FORCE_INSTALLED=1 python -m pytest tests   # 실제 KiCad 연동 전체 테스트 스위트
```

자세한 테스트 가이드는 [`docs/TESTING.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/TESTING.md)를 참조하세요.

---

## 라이선스

GPL-3.0-or-later. `kicad-cli-extended`는 독립적인 오픈소스 프로젝트이며 KiCad 프로젝트와 제휴하거나 공식 보증을 받은 관계가 아닙니다. KiCad는 KiCad 프로젝트의 상표입니다. 자세한 내용은 [LICENSE](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE)를 확인하세요.
