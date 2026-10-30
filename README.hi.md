<p align="center"><img src="https://raw.githubusercontent.com/Hybirdss/kicad-cli-extended/main/docs/assets/banner.png" alt="kicad-cli-extended" width="820"></p>

<div align="center">

# kicad-cli-extended (`kce`)

**AI एजेंटों और हार्डवेयर ऑटोमेशन के लिए विस्तारित, KiCad का आधिकारिक `kicad-cli` टूल।**

[![CI](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml/badge.svg)](https://github.com/Hybirdss/kicad-cli-extended/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/pyproject.toml)

[English](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.md) | [한국어](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.ko.md) | [简体中文](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.zh-CN.md) | [繁體中文](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.zh-TW.md) | [日本語](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.ja.md) | [Español](https://github.com/Hybirdss/kicad-cli-extended/blob/main/README.es.md) | **हिन्दी**

[बेंचमार्क](#बेंचमार्क) · [तुलना](#kicad-cli-बनाम-kicad-cli-extended) · [इंस्टॉलेशन](#आवश्यकताएँ-और-इंस्टॉलेशन) · [क्विकस्टार्ट](#अपना-पहला-led-बोर्ड-बनाएँ) · [एजेंट स्किल](#ai-एजेंट-स्किल) · [वर्कफ़्लो](#वर्कफ़्लो) · [कमांड समूह](#कमांड-समूह) · [JSON अनुबंध](#json-आउटपुट-अनुबंध) · [CI और PowerShell](#powershell-और-ci)

</div>

> **क्या आप Altium Designer पर काम कर रहे हैं?** हमारा सहयोगी टूल **[altium-cli-extended (`ace`)](https://github.com/Hybirdss/altium-cli-extended)** देखें। दोनों प्रोजेक्ट GPL-3.0-or-later लाइसेंस का उपयोग करते हैं।

**`kce`** (KiCad CLI Extended) आधिकारिक `kicad-cli` के हर कमांड और विकल्प को पूरी तरह से सपोर्ट करता है, और इसमें स्कीमैटिक एडिटिंग, नेटलिस्ट-आधारित सर्किट निर्माण (`sch apply`/`verify`), PCB अपडेट और सिंक, प्लेसमेंट स्कोरिंग (`pcb score`), राउटिंग/ऑटोराउटिंग, मैन्युफैक्चरिंग पैकेज (JLCPCB/PCBWay), रिवीजन डिफ, ट्रांजैक्शनल प्लान और AI एजेंट स्किल जोड़ता है — वह भी एक सख्त JSON आउटपुट अनुबंध के साथ।

यह वास्तविक **KiCad 10** (`kicad-cli` और KiCad के `pcbnew` Python मॉड्यूल) को चलाता है और लिखी गई प्रत्येक फ़ाइल को `kicad-cli` के माध्यम से पुनः लोड करके सत्यापित करता है ताकि हर बदलाव स्वयं KiCad द्वारा प्रमाणित हो।

---

## बेंचमार्क

`kce` में हमेशा लोड रहने वाले स्किल फ्रंटमैटर टोकन केवल **157 टोकन** हैं (पूरे `SKILL.md` के साथ 3,737 टोकन), जबकि 2026-10-28 को स्टार्स के आधार पर चुने गए दो प्रमुख सार्वजनिक KiCad MCP सर्वरों में केवल टूल परिभाषाओं के लिए **4,927–63,420 टोकन** खर्च होते हैं। पूरी कमांड संदर्भ फ़ाइल (`COMMANDS.md`) केवल ज़रूरत पड़ने पर खोली जाती है (11,238 टोकन)।

2026-10-30 को 16-CPU Linux मशीन पर KiCad 10.0.7, Python 3.11.17, और `tiktoken` 0.14.0 (`o200k_base`) के साथ मापा गया: KiCad के `pic_programmer` डेमो की ताज़ा प्रतियों पर 5 इंटरलीव्ड रन किए गए (प्रत्येक रन 4.00 लोड गेट से नीचे शुरू हुआ: 2.02, 3.38, 3.54, 3.45, 3.36)। अधिक जानकारी के लिए [पद्धति (Method)](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/METHOD.md), [सारांश और रेंज](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/SUMMARY-2026-10-30.md), तथा [सभी रिकॉर्ड किए गए रन](https://github.com/Hybirdss/kicad-cli-extended/blob/main/bench/results/results-2026-10-30.json) देखें।

| दृष्टिकोण | उपलब्ध टूल्स | काम शुरू होने से पहले कॉन्टेक्स्ट टोकन |
|---|---:|---:|
| **`kce` (स्किल फ्रंटमैटर)** | 1 कमांड | **157** |
| `kce` (पूरा `SKILL.md`, फ्रंटमैटर सहित) | 1 कमांड | 3,737 |
| `mixelpixx/KiCAD-MCP-Server` 2.8.3 (चयन के समय 2,995★), सभी टूल्स | 244 | **63,420** |
| `mixelpixx`, `core-toolbox` कॉन्फ़िगरेशन | 37 | **6,445** |
| `lamaalrajih/kicad-mcp` (चयन के समय 531★) | 16 | **4,927** |

| कार्य (`pic_programmer` डेमो पर) | `kce` (टोकन / समय) | `mixelpixx` (टोकन / समय) | `lamaalrajih` (टोकन / समय) |
|---|---|---|---|
| ERC सारांश | 108 / 3.62 s | 69 / 3.09 s | कोई टूल नहीं |
| DRC जाँच | 119 / 4.45 s | 278 / 3.63 s | 109 / 3.40 s |
| BOM निर्यात | 177 / 1.00 s | 111 / 0.59 s | 133 / 0.53 s |
| Gerber + ड्रिल फ़ाइलें | 2,771 / 2.15 s | 645 / 1.43 s | कोई टूल नहीं |

सभी 50 समर्थित रन सफल रहे और डिस्क पर अपेक्षित फ़ाइलें (BOM, Gerber, ड्रिल) या सटीक ERC/DRC उल्लंघन संख्या उत्पन्न की।

---

## `kicad-cli` बनाम `kicad-cli-extended`

KiCad 10.0.7 के `kicad-cli` के `--help` ट्री (43 कमांड, 582 विकल्प; [`kicad_cli_spec.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/src/kicad_cli_extended/utils/kicad_cli_spec.json) में संग्रहीत) पर आधारित तुलना:

| क्षमता | `kicad-cli` | `kicad-cli-extended` (`kce`) |
|---|:-:|:-:|
| एक्सपोर्ट और जाँच (ERC, DRC, Gerber, drill, BOM, STEP, PDF, render, …) | ✓ | ✓ सभी 43 कमांड और हर विकल्प पास-थ्रू समर्थित |
| हर कमांड के लिए संरचित JSON आउटपुट | केवल `erc`, `drc`, `export stats`, `import` की रिपोर्ट फ़ाइलें | ✓ एकसमान JSON अनुबंध और त्रुटि श्रेणियाँ |
| स्कीमैटिक एडिटिंग: सिंबल प्लेसमेंट, वायर, लेबल, एनोटेशन | | ✓ |
| नेटलिस्ट-आधारित सर्किट निर्माण (`sch apply` / `sch verify`) | केवल नेटलिस्ट एक्सपोर्ट | ✓ इंटेंट JSON, पैच/सबसेट और पहचान सत्यापन |
| स्कीमैटिक से PCB अपडेट करना (`pcb update`) | केवल `drc --schematic-parity` जाँच | ✓ स्थिर UUID, पैड विस्थापन और ट्रैक निरीक्षण |
| प्लेसमेंट स्कोरिंग और सीमित सुधार | | ✓ `score`, `legalize`, `place near` |
| राउटिंग, ऑटोराउटिंग, कॉपर ज़ोन, बोर्ड आउटलाइन | केवल `drc` / प्लॉट में ज़ोन रीफ़िल | ✓ |
| डिफरेंशियल पेयर, लंबाई/स्क्यू नियम और ट्यूनिंग | केवल DRC जाँच | ✓ नियम, कपल्ड राउटिंग, मिएंडर (Meanders) |
| वाया फ़ेंस/स्टिचिंग, टियरड्रॉप्स, रूल एरिया | | ✓ |
| नेट क्लासेस और डिज़ाइन नियम | केवल पढ़ने योग्य | ✓ पढ़ना और लिखना |
| फ़ैब पैकेज (JLCPCB, PCBWay, OSH Park) | केवल Jobsets, कोई वेंडर प्रीसेट नहीं | ✓ स्टेज्ड एटॉमिक रिलीज़ पैकेज और BOM/CPL गेट्स |
| प्रीव्यू और रिवीजन डिफ (`project compare`) | केवल रेंडर और प्लॉट | ✓ ज्योमेट्री डिफ, अनुमति सूची (`--allow`), विज़ुअल बंडल |
| एटॉमिक प्लान (`run`) और नामित चेकपॉइंट (`session`) | | ✓ प्री-कमिट स्वीकृति शर्तें और रीस्टोर |
| रेजिडेंट JSON-RPC वर्कर (`serve`) | | ✓ वॉर्म `pcbnew` वर्कर, डेडलाइन और कैंसिलेशन |
| लाइब्रेरी खोज, कस्टम सिंबल/फुटप्रिंट और विज़ार्ड | | ✓ पिन/पैड/3D मॉडल जाँच |
| पैनलाइज़ेशन (Panelization) | | ✓ माउस बाइट्स और V-स्कोर अलग बोर्ड में |
| Altium / Eagle और अन्य बोर्ड इम्पोर्ट | ✓ बोर्ड रूपांतरण | ✓ टार्गेट चयन, रीड-बैक और स्रोत-संरक्षण रिपोर्ट |
| KiCad IPC API के माध्यम से लाइव GUI नियंत्रण | | ✓ |
| SPICE सिमुलेशन (`ngspice`) | | ✓ |
| AI एजेंट स्किल (`kce skill install`) | | ✓ |

---

## आवश्यकताएँ और इंस्टॉलेशन

* **KiCad 10.x** (`kicad-cli` + इसके साथ आने वाला `pcbnew` Python मॉड्यूल)।
* **Python 3.10+** (`kce` के लिए; `pcbnew` स्वचालित रूप से खोजे गए KiCad के अपने इंटरप्रेटर में चलता है)।
* *नोट:* PLY 3D एक्सपोर्ट के लिए OCCT 7.7 या नए के साथ बना KiCad आवश्यक है; पुराने बिल्ड STEP एक्सपोर्ट कर सकते हैं।

| ऑपरेटिंग सिस्टम | KiCad 10 इंस्टॉल करें |
|---|---|
| **Windows** | [आधिकारिक इंस्टॉलर](https://www.kicad.org/download/windows/), `choco install kicad`, या `winget install KiCad.KiCad` |
| **macOS** | `brew install --cask kicad` (या `.dmg` फ़ाइल) |
| **Ubuntu / Debian** | `sudo add-apt-repository ppa:kicad/kicad-10.0-releases && sudo apt install kicad` |
| **Fedora / Arch** | `sudo dnf install kicad` / `sudo pacman -S kicad kicad-library` |
| **Flatpak** | `flatpak install flathub org.kicad.KiCad` (स्वचालित रूप से पहचाना जाता है) |

सार्वजनिक रिपॉजिटरी से `kce` इंस्टॉल करें:

```bash
uv tool install git+https://github.com/Hybirdss/kicad-cli-extended      # अनुशंसित
# या
pipx install git+https://github.com/Hybirdss/kicad-cli-extended
```

पर्यावरण की जाँच करें और डिफ़ॉल्ट लाइब्रेरी टेबल इनिशियलाइज़ करें (केवल पहली बार):

```bash
kce --json info doctor          # kicad-cli, pcbnew इंटरप्रेटर, लाइब्रेरी डायरेक्टरीज़: "ok": true
kce lib init-global-tables      # केवल पहली बार: KiCad की डिफ़ॉल्ट लाइब्रेरी टेबल्स
```

---

## अपना पहला LED बोर्ड बनाएँ

यह उदाहरण 2-पिन कनेक्टर से संचालित प्रतिरोध-सीमित LED बोर्ड (3.3 V सप्लाई, 330 Ω रेसिस्टर) बनाता है। एक खाली डायरेक्टरी में शुरू करें:

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

**क्या सत्यापित करें:**
* **3 कंपोनेंट, 3 दो-पिन नेट्स**, शून्य ERC/DRC त्रुटियाँ और कोई अनकनेक्टेड या पैरिटी समस्या नहीं।
* `pcb score` में कोई कोर्टयार्ड ओवरलैप या आउटलाइन से बाहर का कंपोनेंट नहीं होना चाहिए।
* `fab package` में `complete: true`, खाली `failed_steps`, और `outputs/fab-jlcpcb/` के अंतर्गत फ़ाइलों की सूची होनी चाहिए।

---

## ECO की समीक्षा और फ़ैब पैकेज रिलीज़ करना

नेट `VCC` पर `U2` के पास नया कैपेसिटर `C10` जोड़ने वाले सत्यापित स्कीमैटिक ECO के लिए, PCB का चेकपॉइंट बनाएँ, सिंक का पूर्वावलोकन करें, और प्लेसमेंट व राउटिंग को संबंधित कंपोनेंट तक सीमित रखें:

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

* **सिंक सुरक्षा:** लागू करने से पहले ड्राई-रन में `replaced[].pads_moved`, `unmatched_pins` और `tracks_ending_on_old_pads` की जाँच करें।
* **स्कोप्ड गेट्स:** `project compare --fail-on-unexpected` अनुमति सूची (`--allow`) के बाहर किसी भी बदलाव को एग्जिट कोड `4` के साथ अस्वीकार कर देता है।
* **असेंबली रिलीज़:** रिलीज़ से पहले `fab parts check --offline` और `fab bom-check --require MPN,LCSC,Footprint` चलाएँ, फिर `fab package --preset jlcpcb --require-lcsc --require-rev v1.3.0 --manifest --name-suffix "{rev}-{preset}" --fail-on-drc -o RELEASE` का उपयोग करें।

---

## AI एजेंट स्किल

`kce` में एक अंतर्निहित एजेंट स्किल (`SKILL.md` + `COMMANDS.md`) शामिल है जो कोडिंग एजेंटों को ये सभी वर्कफ़्लो और सत्यापन नियम सिखाती है।

```bash
kce skill install                                 # मशीन पर पाए गए सभी एजेंट + ~/.agents/skills में इंस्टॉल करें
kce skill install --target claude --target codex --dry-run
kce skill install --dir ./.claude/skills          # प्रोजेक्ट-लोकल इंस्टॉलेशन
kce skill status
```

---

## वर्कफ़्लो

[इंस्टॉल की गई स्किल](https://github.com/Hybirdss/kicad-cli-extended/blob/main/skills/kicad-cli-extended/SKILL.md#choose-a-scenario-recipe) में बारह ट्रिगर-आधारित रेसिपी हैं, जिनका पूरा विवरण [`docs/AGENT-RECIPES.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/AGENT-RECIPES.md) में दिया गया है।

| लक्ष्य | मुख्य कमांड्स | अनुभाग |
|---|---|---|
| नया बोर्ड बनाना और फ़ैब फ़ाइलें निकालना | `project new` → `sch apply` → `pcb update` → `fab package` | [पहला बोर्ड और फ़ैब्रिकेशन](#पहला-बोर्ड-और-फ़ैब्रिकेशन) |
| नेटलिस्ट JSON स्पेसिफिकेशन से चरणबद्ध डिज़ाइन | `session checkpoint --immutable` → `run stage.json` | [स्पेसिफिकेशन से नेटलिस्ट-आधारित बोर्ड](#स्पेसिफिकेशन-से-नेटलिस्ट-आधारित-बोर्ड) |
| सीमित ECO लागू और सत्यापित करना | `pcb update --keep-extra --lock-existing` → `project compare` | [मौजूदा प्रोजेक्ट में इंजीनियरिंग बदलाव (ECO)](#मौजूदा-प्रोजेक्ट-में-इंजीनियरिंग-बदलाव-eco) |
| CI में PR जाँचना और हार्डवेयर रिलीज़ करना | `check erc` → `check drc` → `fab bom-check` → `fab package` | [सतत एकीकरण (CI) और टैग की गई रिलीज़](#सतत-एकीकरण-ci-और-टैग-की-गई-रिलीज़) |
| वॉर्म `pcbnew` वर्कर के साथ प्लेसमेंट खोज | `serve` → `run trial.json` → `session restore best` | [वॉर्म वर्कर के साथ प्लेसमेंट खोज](#वॉर्म-वर्कर-के-साथ-प्लेसमेंट-खोज) |
| अनुपलब्ध सिंबल/फुटप्रिंट बनाना और जाँचना | `lib sym new` → `lib fp wizard` → `lib link-check` → `lib fp check` | [लाइब्रेरी में अनुपलब्ध पुर्ज़े](#लाइब्रेरी-में-अनुपलब्ध-पुर्ज़े) |
| बाहरी बोर्ड इम्पोर्ट, SPICE, हाई-स्पीड राउटिंग और पैनल | `project import` / `sim` / `pcb diff-pair` / `pcb panelize` | [बाहरी बोर्ड, सिमुलेशन, हाई-स्पीड राउटिंग और पैनल](#बाहरी-बोर्ड-सिमुलेशन-हाई-स्पीड-राउटिंग-और-पैनल) |

### पहला बोर्ड और फ़ैब्रिकेशन

ऊपर दिए गए LED क्विकस्टार्ट का पालन करें और असेंबली के लिए सत्यापित सप्लायर पार्ट नंबर चुनें। कनेक्टर किनारों को `pcb move` और `pcb lock` से रखें और `project rules` व `project netclass` से फ़ैब्रिकेशन सीमाएँ निर्धारित करें।

### स्पेसिफिकेशन से नेटलिस्ट-आधारित बोर्ड

स्वीकृत इलेक्ट्रिकल इंटेंट को प्रोजेक्ट के अंदर JSON में रखें और प्रत्येक चरण को एटॉमिक `run` प्लान के रूप में चलाएँ, जिसके बाद एक अपरिवर्तनीय (`--immutable`) चेकपॉइंट बनाएँ:

```bash
kce --json -p sensor session checkpoint initial --immutable
kce --json -p sensor --dry-run run schematic-stage.json
kce --json -p sensor run schematic-stage.json
kce --json -p sensor session checkpoint schematic-ok --immutable
kce --json -p sensor run board-stage.json
```

### मौजूदा प्रोजेक्ट में इंजीनियरिंग बदलाव (ECO)

बेसलाइन नेट्स, फुटप्रिंट्स, स्कोर और ERC/DRC रिकॉर्ड करें और `pre-eco` चेकपॉइंट बनाएँ। PCB सिंक करने से पहले स्कीमैटिक प्लान और सख्त इंटेंट सत्यापन (`sch verify --subset --strict`) चलाएँ, और अंत में `project compare checkpoint:pre-eco --geometry --allow ...` से सत्यापित करें।

### सतत एकीकरण (CI) और टैग की गई रिलीज़

[PR टेम्पलेट](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-ci.yml) और [हार्डवेयर रिलीज़ टेम्पलेट](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/examples/hw-release.yml) का उपयोग करें (`fetch-depth: 0`, स्पष्ट ERC/DRC गेट्स, रिवीजन तुलना और BOM जाँच)।

### वॉर्म वर्कर के साथ प्लेसमेंट खोज

`baseline`, `best-000000` (`--immutable`), और `best` चेकपॉइंट बनाएँ और फ़ोरग्राउंड में `kce serve` चलाएँ। शून्य ओवरलैप और DRC पास करने वाले ट्रायल प्लान को ही `best` में सहेजें।

### लाइब्रेरी में अनुपलब्ध पुर्ज़े

`lib sym search` / `lib fp search` से खोजें, और न मिलने पर `lib sym new`, `lib fp new`, या `lib fp wizard` से प्रोजेक्ट-लोकल लाइब्रेरी में बनाएँ। प्लेसमेंट से पहले `lib link-check` और `lib fp check` अवश्य चलाएँ।

### बाहरी बोर्ड, सिमुलेशन, हाई-स्पीड राउटिंग और पैनल

* **बाहरी बोर्ड इम्पोर्ट:** Altium/Eagle बोर्ड को नई डायरेक्टरी में इम्पोर्ट करें और `read_back` की जाँच करें (मूल Altium फ़ाइलों की जाँच के लिए सहयोगी टूल [`ace`](https://github.com/Hybirdss/altium-cli-extended) का उपयोग करें)।
* **SPICE और हाई-स्पीड राउटिंग:** पहले स्क्रैच प्रोजेक्ट में `sim` से सिमुलेट करें। हाई-स्पीड के लिए नियम → डिफरेंशियल पेयर राउटिंग → मिएंडर ट्यूनिंग → वाया फ़ेंस/स्टिचिंग → टियरड्रॉप्स → ज़ोन फ़िल → DRC → लंबाई रिपोर्ट का क्रम अपनाएँ। अंत में `pcb panelize` से अलग बोर्ड में पैनलाइज़ करें।

---

## कमांड समूह

| समूह | कार्य |
|---|---|
| `project` | टेम्पलेट से नया प्रोजेक्ट, बाहरी बोर्ड इम्पोर्ट, जानकारी, फ़ाइलें, टेक्स्ट वेरिएबल्स, नेट क्लासेस, डिज़ाइन नियम, लाइब्रेरी टेबल्स, वेरिएंट्स, आर्काइव, रिवीजन तुलना |
| `sch` | सिंबल रखना/हटाना/घुमाना, फ़ील्ड्स, वायर, लेबल, पावर, नो-कनेक्ट, शीट्स, एनोटेट, नेट्स, इंटेंट `apply`/`verify`; `kicad-cli` `erc`, `export *`, `upgrade` |
| `pcb` | निरीक्षण, स्कीमैटिक से अपडेट, स्कोर, लीगलाइज़, पास रखना (`place near`), राउट, ऑटोराउट, डिफ पेयर्स, लंबाई ट्यूनिंग, वाया स्टिचिंग/फ़ेंसिंग, टियरड्रॉप्स, ज़ोन और कीपआउट, आउटलाइन, लेयर्स, 3D मॉडल, पैनलाइज़ेशन, बारकोड, होल्स, फ़िडुशियल, DSN/SES; `kicad-cli` `drc`, `render`, `export *`, `import`, `upgrade` |
| `lib` | सिंबल/फुटप्रिंट खोज और निरीक्षण, फुटप्रिंट सुझाव, नया बनाना/विज़ार्ड, पिन/पैड जाँच, पोर्टेबल 3D मॉडल |
| `check` | ERC, DRC (ज़ोन रीफ़िल + स्कीमैटिक पैरिटी), सुधार संकेतों के साथ `diagnose` |
| `fab` | मैन्युफैक्चरिंग पैकेज (`generic`, `jlcpcb`, `pcbway`, `oshpark`), पार्ट्स/BOM जाँच, कीमत और रिवीजन गेट्स, मैनिफ़ेस्ट |
| `jobset` | `.kicad_jobset` फ़ाइलें बनाना और चलाना |
| `preview` | `kicad-cli` द्वारा रेंडर किए गए SVG/PNG प्रीव्यू, रिवीजन डिफ बंडल, लाइव प्रीव्यू सेशन |
| `gui` | IPC API के माध्यम से चलते हुए KiCad GUI को नियंत्रित करना: स्टेटस, चयन, एक्शन, सेव/रीलोड/रीफ़िल |
| `sim` | `ngspice` के साथ SPICE सिमुलेशन: डायरेक्टिव, मॉडल, नेटलिस्ट, रन |
| `session` | स्टेटस, अनडू/रीडू/हिस्ट्री, सेव/डिस्कार्ड, चेकपॉइंट बनाना/सूची/रीस्टोर/हटाना |
| `run` | प्री-कमिट स्वीकृति शर्तों के साथ एटॉमिक मल्टी-स्टेप JSON प्लान चलाना |
| `serve` | वॉर्म `pcbnew` वर्कर, डेडलाइन और कैंसिलेशन के साथ stdio पर रेजिडेंट JSON-RPC 2.0 सर्वर |
| `errors` | स्थिर त्रुटि प्रकार, श्रेणियाँ, एग्जिट कोड और रिकवरी कमांड्स की सूची |
| `raw` | किसी भी KiCad फ़ाइल पर S-expression क्वेरी/एडिट; `pcbnew` Python चलाना |
| `repl` | इंटरैक्टिव शेल (कोई सबकमांड न देने पर डिफ़ॉल्ट) |
| `info` / `skill` | `doctor` जाँच, क्षमताएँ, संस्करण, कमांड मेटाडेटा और ऑफ़लाइन स्कीमा / AI एजेंट स्किल इंस्टॉल करना |

---

## JSON आउटपुट अनुबंध

* **सफलता (`0`):** `--json` देने पर `stdout` पर एक JSON दस्तावेज़ प्रिंट होता है।
* **विफलता (`1`–`7`):** `stderr` पर [`schemas/error.schema.json`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/schemas/error.schema.json) द्वारा सत्यापित एक JSON त्रुटि ऑब्जेक्ट प्रिंट होता है।
* **त्रुटि श्रेणियाँ और एग्जिट कोड:**

  | श्रेणी (`category`) | एग्जिट कोड | अर्थ | प्रमुख `type` मान |
  |---|:-:|---|---|
  | `input` | 2 | गलत आर्ग्यूमेंट, अज्ञात रेफरेंस या पिन, गायब या खराब फ़ाइल, लॉक किया गया आइटम | `UsageError`, `KicadInputError`, `KeyError`, `ValueError`, `SexprError`, `FileNotFoundError`, `FileExistsError`, `ItemLocked`, `RuntimeError` |
  | `design` | 3 | अस्वीकृत क्योंकि परिणाम डिज़ाइन नियमों के विरुद्ध होगा | `NetMergeError`, `NetSplitError`, `RouteBlocked`, `DesignMismatch`, `AcceptanceFailed`, `PlacementBlocked`, `JobsetFailed`, `KicadCheckFailed`, `RuntimeError` (ERC/DRC विफल) |
  | `validation` | 4 | KiCad ने सेव की गई फ़ाइल को अस्वीकार किया (बदलाव रोलबैक हो गया) या तुलना गेट में अनपेक्षित बदलाव मिले | `ValidationError` |
  | `crash` | 5 | `kicad-cli` या `pcbnew` वर्कर क्रैश हो गया | `KicadCrash` |
  | `timeout` | 6 | `kicad-cli` ने `KCE_TIMEOUT` (डिफ़ॉल्ट 1800 सेकंड) पार कर लिया या वर्कर टाइमआउट हो गया | `KicadTimeout`, `ToolTimeout` |
  | `environment` | 7 | KiCad या अन्य टूल नहीं मिला, या OS-स्तरीय I/O विफलता | `KicadNotFound`, `OSError`, `RuntimeError` |
  | `internal` | 1 | `kce` में आंतरिक बग (`details.traceback` में स्टैक उपलब्ध) | `KicadError`, `RuntimeError`, `InternalError`, `TypeError`, `IndexError` |

---

## PowerShell और CI

PowerShell में आर्ग्यूमेंट ऐरे का उपयोग करें और शेल लूप के बजाय `run` प्लान का उपयोग करें:

```powershell
$KCE = "--json", "-p", "led"
& kce @KCE info doctor
& kce @KCE session checkpoint before-eco --immutable
& kce @KCE --dry-run run eco.json
& kce @KCE run eco.json
# ऋणात्मक कॉमा-सेपरेटेड मानों को '=' से जोड़ें: --rotate=-45,0,45
```

CI चलाने से पहले `.gitignore` और `.gitattributes` में ये नियम जोड़ें:

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

## विकास और परीक्षण (Development and tests)

```bash
git clone https://github.com/Hybirdss/kicad-cli-extended && cd kicad-cli-extended
python -m pip install -e ".[test]"
python scripts/fetch_fixtures.py               # एंड-टू-एंड परीक्षणों के लिए KiCad डेमो प्रोजेक्ट्स
python -m pytest tests/test_core.py            # यूनिट टेस्ट (KiCad की आवश्यकता नहीं)
python scripts/gen_skill.py --check            # जनरेटेड स्किल/रेफरेंस/स्कीमा गेट
KCE_FORCE_INSTALLED=1 python -m pytest tests   # वास्तविक KiCad के साथ पूरा टेस्ट सूट
```

अधिक विवरण [`docs/TESTING.md`](https://github.com/Hybirdss/kicad-cli-extended/blob/main/docs/TESTING.md) में देखें।

---

## लाइसेंस

GPL-3.0-or-later। `kicad-cli-extended` एक स्वतंत्र ओपन-सोर्स प्रोजेक्ट है, जो KiCad प्रोजेक्ट से संबद्ध या समर्थित नहीं है। KiCad, KiCad प्रोजेक्ट का एक ट्रेडमार्क है। विवरण के लिए [LICENSE](https://github.com/Hybirdss/kicad-cli-extended/blob/main/LICENSE) देखें।
