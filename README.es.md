<p align="center"><img src="https://raw.githubusercontent.com/Hybirdss/kicad-cli-extended/main/docs/assets/banner.png" alt="kicad-cli-extended" width="820"></p>

<div align="center">

# kicad-cli-extended (`kce`)

**El `kicad-cli` oficial de KiCad, extendido para agentes de IA y automatización de hardware.**

[![CI](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml/badge.svg)](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/pyproject.toml)

[English](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.md) | [한국어](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.ko.md) | [简体中文](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.zh-CN.md) | [繁體中文](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.zh-TW.md) | [日本語](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.ja.md) | **Español** | [हिन्दी](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.hi.md)

[Benchmark](#benchmark) · [Comparación](#kicad-cli-frente-a-kicad-cli-extended) · [Instalación](#requisitos-e-instalación) · [Inicio Rápido](#construye-tu-primera-placa-led) · [Skill para IA](#skill-para-agentes-de-ia) · [Flujos de Trabajo](#flujos-de-trabajo) · [Comandos](#grupos-de-comandos) · [Contrato JSON](#contrato-de-salida-json) · [CI y PowerShell](#powershell-y-ci)

</div>

> **¿Trabajas con Altium Designer?** Consulta **[altium-cli-extended (`ace`)](https://github.com/Hybirdss/altium-cli-extended)**, la herramienta complementaria. Ambos proyectos utilizan la licencia GPL-3.0-or-later.

**`kce`** (KiCad CLI Extended) incluye todos los comandos y opciones del `kicad-cli` oficial y los amplía con edición de esquemáticos, ensamblaje de circuitos guiado por netlist (`sch apply`/`verify`), sincronización de PCB, puntuación de emplazamiento (`pcb score`), ruteo manual y automático, paquetes de fabricación (JLCPCB/PCBWay), diferencias entre revisiones, planes transaccionales y un skill para agentes de IA, todo bajo un contrato JSON estricto.

Controla **KiCad 10** real (`kicad-cli` y el módulo Python `pcbnew` de KiCad) y vuelve a cargar cada archivo escrito mediante `kicad-cli`, de modo que el propio KiCad valida cada cambio antes de confirmarlo.

---

## Benchmark

`kce` utiliza solo **157 tokens de skill siempre cargados** (3.737 con todo el archivo `SKILL.md`), frente a los **4.927–63.420 tokens de definición de herramientas** de los dos servidores MCP públicos de KiCad con más estrellas seleccionados el 2026-10-28. La referencia completa de comandos cuesta 11.238 tokens solo cuando se abre bajo demanda.

Medido el 2026-10-30 con KiCad 10.0.7, Python 3.11.17 en una máquina Linux de 16 CPU y `tiktoken` 0.14.0 (`o200k_base`): 5 repeticiones intercaladas sobre copias limpias de la demo `pic_programmer` de KiCad. Todas las repeticiones comenzaron por debajo del umbral de carga 4,00 (cargas registradas: 2,02, 3,38, 3,54, 3,45, 3,36). Consulta el [Método](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/METHOD.md), el [resumen y rangos](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/SUMMARY-2026-10-30.md) y [todas las ejecuciones registradas](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/results-2026-10-30.json).

| Enfoque | Herramientas Expuestas | Tokens de Contexto Antes de Empezar |
|---|---:|---:|
| **`kce` (frontmatter del skill)** | 1 comando | **157** |
| `kce` (`SKILL.md` completo, incluye frontmatter) | 1 comando | 3.737 |
| `mixelpixx/KiCAD-MCP-Server` 2.8.3 (2.995★ en la selección), todas las herramientas | 244 | **63.420** |
| `mixelpixx`, configuración `core-toolbox` | 37 | **6.445** |
| `lamaalrajih/kicad-mcp` (531★ en la selección) | 16 | **4.927** |

| Tarea (en la demo `pic_programmer`) | `kce` (tokens / tiempo) | `mixelpixx` (tokens / tiempo) | `lamaalrajih` (tokens / tiempo) |
|---|---|---|---|
| Resumen ERC | 108 / 3,62 s | 69 / 3,09 s | sin herramienta |
| DRC | 119 / 4,45 s | 278 / 3,63 s | 109 / 3,40 s |
| BOM | 177 / 1,00 s | 111 / 0,59 s | 133 / 0,53 s |
| Gerber + taladros (drill) | 2.771 / 2,15 s | 645 / 1,43 s | sin herramienta |

Las 50 ejecuciones admitidas finalizaron con éxito y generaron los archivos esperados en disco (BOM, Gerber, taladros) o el recuento de violaciones ERC/DRC.

---

## `kicad-cli` frente a `kicad-cli-extended`

Derivado del árbol `--help` de `kicad-cli` en KiCad 10.0.7 (43 comandos, 582 opciones; almacenado en [`kicad_cli_spec.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/src/kicad_cli_extended/utils/kicad_cli_spec.json)).

| Capacidad | `kicad-cli` | `kicad-cli-extended` (`kce`) |
|---|:-:|:-:|
| Exportaciones y comprobaciones (ERC, DRC, Gerber, drill, BOM, STEP, PDF, render, …) | ✓ | ✓ Los 43 comandos y todas las opciones disponibles |
| Salida JSON estructurada para cada comando | Solo informes JSON en `erc`, `drc`, `export stats`, `import` | ✓ Contrato JSON uniforme y categorías de error |
| Edición de esquemáticos: colocar, cablear, etiquetar, anotar | | ✓ |
| Ensamblaje guiado por netlist (`sch apply` / `sch verify`) | Solo exportación de netlist | ✓ JSON de intención, parches y verificación de identidad |
| Actualizar PCB desde el esquemático (`pcb update`) | Solo comprobación con `drc --schematic-parity` | ✓ UUID estables, inspección de pads y pistas |
| Puntuación de emplazamiento y reparación acotada | | ✓ `score`, `legalize`, `place near` |
| Ruteo, autoruteo, zonas de cobre, contorno de placa | Solo relleno de zonas en `drc` / trazados | ✓ |
| Pares diferenciales, reglas de longitud/skew y ajuste (tuning) | Solo comprobaciones DRC | ✓ Reglas, ruteo acoplado, meandros |
| Vallas/costura de vías (via stitching), teardrops, áreas de reglas | | ✓ |
| Clases de red (netclasses) y reglas de diseño | Solo lectura | ✓ Lectura y escritura |
| Paquetes de fabricación (JLCPCB, PCBWay, OSH Park) | Jobsets, sin preajustes de fabricante | ✓ Paquetes atómicos en staging y validación BOM/CPL |
| Vistas previas y diferencias entre revisiones (`project compare`) | Solo renders y trazados | ✓ Diffs geométricos, listas permitidas (`--allow`), paquetes visuales |
| Planes atómicos (`run`) y puntos de control con nombre (`session`) | | ✓ Predicados de aceptación previos al commit y restauración |
| Worker JSON-RPC residente (`serve`) | | ✓ Worker `pcbnew` precalentado, tiempos límite, cancelación |
| Búsqueda en librerías, creación/asistentes de símbolos y huellas | | ✓ Verificación de pines, pads y modelos 3D |
| Panelización | | ✓ Mouse bites y V-score hacia una placa separada |
| Importación de placas Altium / Eagle y otros formatos | ✓ Conversión de placa | ✓ Selección de destino, lectura de verificación e informe de preservación |
| Control de GUI en vivo mediante la API IPC de KiCad | | ✓ |
| Simulación SPICE (`ngspice`) | | ✓ |
| Skill para agentes de IA (`kce skill install`) | | ✓ |

---

## Requisitos e instalación

* **KiCad 10.x** (`kicad-cli` + su módulo Python `pcbnew`, incluidos con KiCad).
* **Python 3.10+** para `kce`. `pcbnew` se ejecuta en el propio intérprete de KiCad, detectado automáticamente.
* *Nota:* La exportación 3D en formato PLY requiere KiCad compilado con OCCT 7.7 o superior; las versiones anteriores aún pueden exportar STEP.

| Sistema Operativo | Instalar KiCad 10 |
|---|---|
| **Windows** | [Instalador oficial](https://www.kicad.org/download/windows/), `choco install kicad` o `winget install KiCad.KiCad` |
| **macOS** | `brew install --cask kicad` (o el archivo `.dmg`) |
| **Ubuntu / Debian** | `sudo add-apt-repository ppa:kicad/kicad-10.0-releases && sudo apt install kicad` |
| **Fedora / Arch** | `sudo dnf install kicad` / `sudo pacman -S kicad kicad-library` |
| **Flatpak** | `flatpak install flathub org.kicad.KiCad` (detectado automáticamente) |

Instala `kce` desde el repositorio público:

```bash
uv tool install git+https://github.com/Hybirdss/kicad-cli-extended      # recomendado
# o
pipx install git+https://github.com/Hybirdss/kicad-cli-extended
```

Verifica el entorno e inicializa las tablas de librerías predeterminadas (solo en la primera ejecución):

```bash
kce --json info doctor          # kicad-cli, intérprete pcbnew, directorios de librerías: "ok": true
kce lib init-global-tables      # solo la primera vez: tablas de librerías globales de KiCad
```

---

## Construye tu primera placa LED

Este ejemplo crea una placa LED limitada por resistencia y alimentada mediante un conector de 2 pines (alimentación de 3,3 V, resistencia de 330 Ω). Comienza en un directorio vacío:

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

**Qué verificar:**
* **3 componentes, 3 redes de dos pines**, cero errores ERC/DRC y cero problemas de conectividad o paridad.
* `pcb score` informa cero solapamientos de courtyard y ningún componente fuera del contorno.
* `fab package` devuelve `complete: true`, `failed_steps` vacío y una lista `files` bajo `outputs/fab-jlcpcb/`.

---

## Revisar un ECO y publicar un paquete de fabricación

Para un ECO de esquemático verificado que añade `C10` cerca de `U2` en la red `VCC`, crea un punto de control de la PCB, previsualiza la sincronización y acota el emplazamiento y ruteo al componente afectado:

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

* **Seguridad al sincronizar:** Revisa `replaced[].pads_moved`, `unmatched_pins` y `tracks_ending_on_old_pads` antes de aplicar.
* **Control de alcance:** `project compare --fail-on-unexpected` rechaza cualquier cambio fuera de la lista `--allow` con código de salida `4`.
* **Publicación para ensamblaje:** Ejecuta `fab parts check --offline` y `fab bom-check --require MPN,LCSC,Footprint`, y genera el paquete con `fab package --preset jlcpcb --require-lcsc --require-rev v1.3.0 --manifest --name-suffix "{rev}-{preset}" --fail-on-drc -o RELEASE`.

---

## Skill para agentes de IA

`kce` incluye un skill integrado (`SKILL.md` + `COMMANDS.md`) que enseña a los agentes de IA los flujos de trabajo y controles de verificación descritos en esta guía.

```bash
kce skill install                                 # instala en todos los agentes detectados + ~/.agents/skills
kce skill install --target claude --target codex --dry-run
kce skill install --dir ./.claude/skills          # instalación local en el proyecto
kce skill status
```

---

## Flujos de trabajo

El [skill instalado](https://github.com/Hybirdss/kicad-cli-extended/blob/main/skills/kicad-cli-extended/SKILL.md#choose-a-scenario-recipe) ofrece doce recetas activadas por contexto, detalladas en [`docs/AGENT-RECIPES.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/AGENT-RECIPES.md).

| Objetivo | Comandos Clave | Sección |
|---|---|---|
| Diseñar una placa nueva y exportar archivos fab | `project new` → `sch apply` → `pcb update` → `fab package` | [Primera placa y fabricación](#primera-placa-y-fabricación) |
| Diseño multietapa guiado por netlist JSON | `session checkpoint --immutable` → `run stage.json` | [Placa guiada por netlist desde una especificación](#placa-guiada-por-netlist-desde-una-especificación) |
| Aplicar y verificar un ECO acotado | `pcb update --keep-extra --lock-existing` → `project compare` | [Cambio de ingeniería (ECO) en un proyecto existente](#cambio-de-ingeniería-eco-en-un-proyecto-existente) |
| Validar PRs y publicar versiones de hardware en CI | `check erc` → `check drc` → `fab bom-check` → `fab package` | [Integración continua y lanzamientos etiquetados](#integración-continua-y-lanzamientos-etiquetados) |
| Búsqueda de emplazamiento con worker `pcbnew` activo | `serve` → `run trial.json` → `session restore best` | [Búsqueda de emplazamiento con worker precalentado](#búsqueda-de-emplazamiento-con-worker-precalentado) |
| Crear y verificar símbolos/huellas faltantes | `lib sym new` → `lib fp wizard` → `lib link-check` → `lib fp check` | [Componente ausente en las librerías](#componente-ausente-en-las-librerías) |
| Importar placas externas, SPICE, alta velocidad y paneles | `project import` / `sim` / `pcb diff-pair` / `pcb panelize` | [Placas externas, simulación, ruteo de alta velocidad y paneles](#placas-externas-simulación-ruteo-de-alta-velocidad-y-paneles) |

### Primera placa y fabricación

Sigue el inicio rápido del LED y elige códigos de proveedor verificados para el ensamblaje. Coloca conectores de borde con `pcb move` y `pcb lock`, y configura los límites reales de fabricación con `project rules` y `project netclass`.

### Placa guiada por netlist desde una especificación

Mantén la intención eléctrica aprobada en JSON dentro del proyecto y ejecuta cada etapa mediante un plan `run` atómico seguido de un punto de control inmutable (`--immutable`):

```bash
kce --json -p sensor session checkpoint initial --immutable
kce --json -p sensor --dry-run run schematic-stage.json
kce --json -p sensor run schematic-stage.json
kce --json -p sensor session checkpoint schematic-ok --immutable
kce --json -p sensor run board-stage.json
```

### Cambio de ingeniería (ECO) en un proyecto existente

Registra las redes base, huellas, puntuación y ERC/DRC, y crea el punto de control `pre-eco`. Verifica estrictamente la intención del esquemático (`sch verify --subset --strict`) antes de sincronizar la PCB y comprueba el resultado con `project compare checkpoint:pre-eco --geometry --allow ...`.

### Integración continua y lanzamientos etiquetados

Usa la [plantilla de PR](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-ci.yml) y la [plantilla de lanzamiento de hardware](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-release.yml) con `fetch-depth: 0`, umbrales explícitos de ERC/DRC, comparación de revisiones y validación de BOM/precios.

### Búsqueda de emplazamiento con worker precalentado

Crea los puntos de control `baseline`, `best-000000` (`--immutable`) y `best`, mantén un proceso `kce serve` activo y evalúa planes candidatos exigiendo cero solapamientos y cero errores DRC antes de actualizar `best`.

### Componente ausente en las librerías

Busca nombres normalizados (`lib sym search`, `lib fp search`) o crea componentes locales con `lib sym new`, `lib fp new` o `lib fp wizard` usando rutas `${KIPRJMOD}`. Ejecuta `lib link-check` y `lib fp check` antes de colocarlos.

### Placas externas, simulación, ruteo de alta velocidad y paneles

* **Importación de placas externas:** Importa placas de Altium/Eagle en un directorio nuevo, inspecciona `read_back` y utiliza la herramienta complementaria [`ace`](https://github.com/Hybirdss/altium-cli-extended) para auditar archivos nativos de Altium.
* **Simulación SPICE y alta velocidad:** Simula primero en un proyecto temporal con `sim`. Para bloques de alta velocidad sigue el orden: reglas → ruteo de pares diferenciales → ajuste de longitud (meandros) → vallas/costura de vías → teardrops → relleno de zonas → DRC → informe de longitudes. Paneliza al final en una placa separada con `pcb panelize`.

---

## Grupos de comandos

| Grupo | Qué hace |
|---|---|
| `project` | Crear desde plantillas, importar placas externas, info, archivos, variables de texto, clases de red, reglas de diseño, tablas de librerías, variantes, archivo, comparar revisiones |
| `sch` | Colocar/mover/rotar símbolos, campos, cables, etiquetas, alimentación, no-connects, hojas jerárquicas, anotar, redes, `apply`/`verify` de intenciones; `kicad-cli` `erc`, `export *`, `upgrade` |
| `pcb` | Inspeccionar, actualizar desde esquemático, puntuar, legalizar, colocar cerca, rutear, autorutear, pares diferenciales, ajuste de longitud, costura/vallas de vías, teardrops, zonas y keepouts, contorno, capas, modelos 3D, panelización, códigos de barras, taladros, fiduciales, DSN/SES; `kicad-cli` `drc`, `render`, `export *`, `import`, `upgrade` |
| `lib` | Buscar e inspeccionar símbolos/huellas, sugerir huellas, crear/asistentes, comprobación de pines/pads, modelos 3D portátiles |
| `check` | ERC, DRC (relleno de zonas + paridad con esquemático), `diagnose` con sugerencias de corrección |
| `fab` | Paquetes de fabricación (`generic`, `jlcpcb`, `pcbway`, `oshpark`), validación de componentes/BOM, puertas de precio y revisión, manifiestos |
| `jobset` | Crear y ejecutar archivos `.kicad_jobset` |
| `preview` | Vistas previas SVG/PNG renderizadas por `kicad-cli`, paquetes de diferencias entre revisiones, sesiones en vivo |
| `gui` | Controlar una GUI de KiCad en ejecución mediante su API IPC: estado, selección, acciones, guardar/recargar/rellenar |
| `sim` | Simulación SPICE con `ngspice`: directivas, modelos, netlist, ejecución |
| `session` | Estado, deshacer/rehacer/historial, guardar/descartar, puntos de control (`checkpoint`/`list`/`restore`/`drop`) |
| `run` | Planes JSON atómicos con predicados de aceptación previos al commit |
| `serve` | Servidor JSON-RPC 2.0 residente sobre stdio con worker `pcbnew` precalentado, tiempos límite y cancelación |
| `errors` | Tipos de error estables, categorías, códigos de salida y comandos de recuperación |
| `raw` | Consulta/edición de expresiones S en cualquier archivo KiCad; ejecución de Python en `pcbnew` |
| `repl` | Shell interactivo (predeterminado cuando no se indica subcomando) |
| `info` / `skill` | Diagnóstico `doctor`, capacidades, versiones, metadatos de comandos y esquemas JSON offline / instalar el skill para agentes |

---

## Contrato de salida JSON

* **Éxito (`0`):** `--json` imprime un único documento JSON en `stdout`.
* **Fallo (`1`–`7`):** Emite un objeto JSON en `stderr`, validado por [`schemas/error.schema.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/error.schema.json).
* **Categorías de error y códigos de salida:**

  | categoría (`category`) | salida | significado | valores de `type` |
  |---|:-:|---|---|
  | `input` | 2 | argumentos inválidos, referencia o pin desconocido, archivo faltante o mal formado, elemento bloqueado | `UsageError`, `KicadInputError`, `KeyError`, `ValueError`, `SexprError`, `FileNotFoundError`, `FileExistsError`, `ItemLocked`, `RuntimeError` |
  | `design` | 3 | rechazado porque el resultado violaría el diseño | `NetMergeError`, `NetSplitError`, `RouteBlocked`, `DesignMismatch`, `AcceptanceFailed`, `PlacementBlocked`, `JobsetFailed`, `KicadCheckFailed`, `RuntimeError` (ERC/DRC fallido) |
  | `validation` | 4 | KiCad rechazó un archivo guardado (cambio revertido) o `project compare` detectó cambios no permitidos | `ValidationError` |
  | `crash` | 5 | `kicad-cli` o el worker `pcbnew` terminó abruptamente | `KicadCrash` |
  | `timeout` | 6 | `kicad-cli` superó `KCE_TIMEOUT` (por defecto 1800 s) o el worker excedió su tiempo límite | `KicadTimeout`, `ToolTimeout` |
  | `environment` | 7 | No se encontró KiCad u otra herramienta, o fallo de E/S del sistema operativo | `KicadNotFound`, `OSError`, `RuntimeError` |
  | `internal` | 1 | error interno en `kce` (`details.traceback` contiene la traza) | `KicadError`, `RuntimeError`, `InternalError`, `TypeError`, `IndexError` |

---

## PowerShell y CI

En PowerShell, utiliza arrays de argumentos y planes `run` en lugar de bucles específicos del shell:

```powershell
$KCE = "--json", "-p", "led"
& kce @KCE info doctor
& kce @KCE session checkpoint before-eco --immutable
& kce @KCE --dry-run run eco.json
& kce @KCE run eco.json
# Une valores negativos separados por comas usando '=': --rotate=-45,0,45
```

Añade estas reglas a `.gitignore` y `.gitattributes` antes de ejecutar CI:

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

## Desarrollo y pruebas

```bash
git clone https://github.com/Hybirdss/kicad-cli-extended && cd kicad-cli-extended
python -m pip install -e ".[test]"
python scripts/fetch_fixtures.py               # proyectos demo de KiCad para pruebas E2E
python -m pytest tests/test_core.py            # pruebas unitarias (sin necesidad de KiCad)
python scripts/gen_skill.py --check            # verificación de skill/referencia/esquemas generados
KCE_FORCE_INSTALLED=1 python -m pytest tests   # suite completa contra KiCad real
```

Detalles adicionales en [`docs/TESTING.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/TESTING.md).

---

## Licencia

GPL-3.0-or-later. `kicad-cli-extended` es un proyecto independiente de código abierto, no afiliado ni respaldado por el proyecto KiCad. KiCad es una marca registrada del proyecto KiCad. Consulta [LICENSE](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE).
