# Subtitle Studio · 视频字幕工坊

导入视频 → **本地 Whisper 转写** → **大模型只改错别字** → 导出 SRT / VTT / ASS / TXT / JSON …

一条为「UP 主 / 剪辑师 / 课程制作者」设计的字幕生产线：转写在本地跑（不上传隐私内容），
改错别字交给大模型（但严格禁止它改写句式），最后你自己逐条过一遍就能出片。

---

## 一、它能做什么

| 环节 | 能力 |
|---|---|
| **导入** | 拖拽或选择 mp4/mkv/mov/m4a/mp3… 任意媒体；也可直接导入已有字幕（srt/vtt/ass/lrc/txt/json/md/html） |
| **转写** | faster-whisper（本地 GPU/CPU）、Buzz 的模型、whisper.cpp、云端 Whisper API 四种后端 |
| **纠错** | OpenAI 兼容接口（DeepSeek / Kimi / 通义 / 智谱 / 豆包 / OpenRouter / Ollama / LM Studio…）<br>可写提示词、贴**原始稿件**、维护术语表；只改错别字，绝不动时间轴 |
| **精修** | 播放器 + 时间轴 + 字幕表三向联动，逐条编辑 / 拆分 / 合并 / 平移 / A-B 循环 / 变速 / 撤销重做 |
| **导出** | SRT、VTT、ASS、TXT（纯稿件 / 带时间戳）、JSON（含词级时间戳）、Markdown、HTML、LRC，可批量多选 |
| **其它** | 工程文件 `.ssp` 保存全部状态（原文/改后/词级时间戳）、自动保存、导出前体检、无界面批处理 |

---

## 二、快速开始

```bat
:: 1) 安装依赖（首次）
pip install -r requirements.txt

:: 2) 自检环境（看模型、GPU、依赖是否齐）
python run.py --check

:: 3) 启动
python run.py
```

也可以直接双击 `启动 Subtitle Studio.bat`。

### 第一次使用（3 步）

1. **设置**页 → 选一个模型预设（DeepSeek/OpenAI/Kimi…）→ 填 API Key → 点 **测试连接**。
2. **字幕编辑**页 → 拖入视频 → 点 **转写**。
3. 到 **AI 纠错**页贴入原始稿件/术语表 → **开始纠错** → 回编辑页扫一眼 → **导出成品**。

---

## 三、复用本机已有模型（不重复下载）

faster-whisper 需要 **CTranslate2** 格式的模型。本机会自动扫描这些位置：

```
D:\VideoCaptioner\AppData\models\faster-whisper-*     ← 卡卡字幕助手
%LOCALAPPDATA%\VideoCaptioner\models
%USERPROFILE%\.cache\huggingface\hub
%USERPROFILE%\.cache\modelscope\hub
SSData\models                                          ← 本程序自己的下载目录
```

本机现状（已实测）：

| 位置 | 内容 | 能否直接给 faster-whisper 用 |
|---|---|---|
| `D:\VideoCaptioner\AppData\models\faster-whisper-large-v3-turbo` | 1.55 GB CT2 | ✅ 推荐，8GB 显存很顺 |
| `D:\VideoCaptioner\AppData\models\faster-whisper-large-v3` | 2.95 GB CT2 | ✅ 精度更高、更慢 |
| `D:\VideoCaptioner\AppData\models\faster-whisper-tiny` | 72 MB CT2 | ✅ 快速草稿 |
| `%LOCALAPPDATA%\Buzz\Buzz\Cache\models\whisper\*.pt` | openai-whisper 权重 | ⚠️ 需走「Buzz」引擎，faster-whisper 不能直接读 `.pt` |

在 **设置 → 语音转写引擎** 里点「重新扫描本地模型」即可看到全部命中项；下拉框里带 `[卡卡字幕助手]` 标记的就是零流量可用的模型。

### GPU 报 `cublas64_12.dll is not found` 怎么办

这是个很容易踩的坑：`ctranslate2` 官方 wheel 按 **CUDA 12** 编译，而如果你装的是
CUDA 13 的 torch（如 `+cu132` nightly），`torch\lib` 里只有 `cublas64_13.dll`。
更隐蔽的是 `get_cuda_device_count()` 只看驱动、照样报「有 GPU」，要等真正推理时才炸。

程序启动转写时会自动按下列顺序寻找 CUDA 12 运行库并注册进当前进程，
**不需要复制文件、不需要重装 torch**：

```
1. 设置里手动填写的「CUDA 12 运行库」目录
2. pip 装的 nvidia-cublas-cu12 / nvidia-cudnn-cu12
3. 已安装 torch 的 torch\lib
4. 本机其它软件自带的运行环境（Buzz、卡卡字幕助手的 torch\lib）
5. PATH
```

找不到时会自动降级用 CPU 跑完，并在进度条上说明原因，不会中途崩掉。
想用回 GPU，任选其一：

```bat
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 -i https://pypi.tuna.tsinghua.edu.cn/simple
```

或在设置里点「探测 CUDA」，把目录填成Buzz/卡卡目录下的 `torch\lib`。

本机实测（`python run.py --check` 的输出）：

```
 [✓] GPU 可推理                1 块 GPU，CUDA12 运行库 D:\Buzz\_internal\torch\lib
```

---

## 三·五、实测数据

| 项目 | 结果 |
|---|---|
| 素材 | 8 分 29 秒中文口播配音 |
| 模型 | `faster-whisper-large-v3-turbo`（复用卡卡字幕助手已有模型，0 下载） |
| 硬件 | RTX 5060 Laptop 8GB，`cuda` + `float16` |
| 耗时 | **26.5 秒**（≈19× 实时） |
| 产出 | 240 条字幕，240/240 带词级时间戳，语言判定 zh（置信 1.00） |
| 格式 | SRT/VTT/JSON 解析回读条数一致；智能断句 240→243 条 |

---

## 四、关于「只改错别字」的工程保障

把整段字幕丢给大模型，最常见的翻车是：它开始润色、翻译、合并句子、少回几行、
或者把 `[123]` 当成 markdown 列表吃掉。本程序的做法：

1. **带编号往返**：发给模型的是 `[0] 文本`、`[1] 文本`…，回来按编号解析，天然对齐。
2. **行数校验**：条数不对 / 编号缺失 → 自动用更严格的提示词重试（默认 2 次）。
3. **长度突变拦截**：单行长度暴涨/暴跌（默认 >2.2× 或 <0.45×）判为可疑。
4. **翻译检测**：原文中文占比高、结果中文占比骤降 → 判为「被翻译了」，拒收。
5. **失败标红不覆盖**：可疑的行保留原文并标记 `待复查`，导出页会汇总提示。
6. **原文永久保留**：每条 cue 都有 `original_text`，随时对比、单条回滚或一键全部回滚。

> 提示词在 **设置** 页可完全自定义，占位符 `{payload}` `{count}` `{first}` `{last}`
> `{glossary_block}` `{script_block}` 必须保留其一。

---

## 五、快捷键

| 键 | 作用 | 键 | 作用 |
|---|---|---|---|
| `空格` | 播放/暂停 | `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做 |
| `←` `→` | ±5 秒 | `Ctrl+G` | 开始转写 |
| `Shift+←/→` | ±1 秒 | `Ctrl+S` | 保存工程 |
| `,` `.` | ±1 帧 | `Ctrl+I` | 导入字幕 |
| `[` `]` | 选中字幕 ±0.1s | `Ctrl+F` | 搜索/筛选 |
| `F2` / `F3` | 拆分 / 合并 | `Ctrl+D` / `Del` | 删除选中 |

双击字幕行 → 从该条开始播放。右键 → 拆分、合并、时间平移、标记复查、复制文本等。

---

## 六、批量 / 无界面模式

```bat
:: 全流程：转写 + 纠错 + 导出（顺带产出 .ssp 工程，便于回 GUI 精修）
python run.py --headless --video "D:\素材\a.mp4" --out "D:\成片\a.srt"

:: 只要原始识别结果，不调大模型
python run.py --headless --video "D:\素材\a.mp4" --out "D:\成片\a.srt" --no-fix
```

---

## 七、目录结构

```
subtitle-studio/
├─ VERSION                    版本号唯一真源
├─ CHANGELOG.md               更新日志（release.py 自动维护）
├─ release.py                 发版：改版本→CHANGELOG→git tag→打包
├─ run.py                     启动脚本
├─ 启动 Subtitle Studio.bat   双击启动
├─ requirements.txt
├─ build.spec                 PyInstaller 打包配置（版本资源自动注入）
└─ sstudio/
   ├─ version.py              版本读取：源码/打包双环境 + git 溯源
   ├─ core/                   无 UI 依赖的纯逻辑层
   │  ├─ model.py             Cue / CueDocument、时间码解析、拆分合并、清理
   │  ├─ formats.py           SRT/VTT/ASS/LRC/TXT/JSON/MD/HTML 读写 + 自动识别
   │  ├─ media.py             时长探测、ffmpeg/PyAV 抽音频
   │  ├─ transcriber.py       四种转写后端 + 本地模型自动发现
   │  ├─ cuda_rt.py           自动定位 CUDA 12 运行库（复用他机已有的 cublas64_12）
   │  ├─ llm.py               提示词组装、编号往返解析、严格校验、并发批次
   │  ├─ config.py            配置持久化（含便携模式回退）
   │  └─ _buzz_worker.py      在 Buzz 自带 Python 里跑 openai-whisper 的子进程脚本
   ├─ ui/
   │  ├─ main_window.py       FluentWindow 总控、进度、工程存取、拖拽
   │  ├─ editor_page.py       播放器 + 时间轴 + 字幕表 + 工具条
   │  ├─ cue_table.py         多行可编辑表格、状态着色、右键菜单
   │  ├─ timeline.py          自绘时间轴（点击定位、框选、状态配色）
   │  ├─ player.py            QMediaPlayer 封装（倍速/A-B 循环/音量）
   │  ├─ fix_page.py          AI 纠错页
   │  ├─ export_page.py       导出页 + 导出前体检
   │  ├─ settings_page.py     模型/提示词/转写设置
   │  ├─ workers.py           QThread 包装
   │  └─ preview.py, theme.py
   ├─ cli_pipeline.py         无界面流水线
   └─ selfcheck.py            环境自检
```

---

## 七·五、跑测试

```bat
python tests\run_all.py            :: 全套（含一次真实转写，约 1 分钟）
python tests\run_all.py --quick    :: 跳过需要模型的转写用例，10 秒内出结果
```

| 用例 | 覆盖 |
|---|---|
| `formats.py` | 9 种格式导出→导入往返、脏文件容错、时间码、无标点长文切分、导入格式自动识别 |
| `llm_logic.py` | 提示词渲染、各种编号风格、模型杂讯/折行/礼貌语、严格校验的放行与拦截边界 |
| `fix_pipeline.py` | 并发批次、限流重试、编号缺失重试、翻译/灌水拦截、服务全挂与取消 |
| `engines.py` | 模型发现、引擎可用性、CUDA 运行库探测、工程往返、时间轴清理、`split_long` 随机压测 |
| `gui.py` | 离屏构建四页、编辑/撤销/重做、时间平移、LLM 写回与撤销、设置读写、工程存取 |
| `version.py` | VERSION 真源一致、语义化解析边界、模拟冻结环境读 VERSION/BUILDINFO、git 缺失不崩、发版校验、.gitignore 兜底 |
| `transcribe.py` | 真实端到端转写（缺依赖或素材时自动跳过，不会误报失败） |

测试离线且自隔离：LLM 用假响应驱动，配置写到临时目录（靠 `SUBTITLE_STUDIO_HOME`），
绝不读写你真实的设置与 API Key。

---

## 八、常见问题

**Q：转写报「本地找不到模型，且无法联网下载」**
设置 → 模型 → 重新扫描本地模型 → 选一个带 `[卡卡字幕助手]` 的项。

**Q：GPU 跑不动 / 显存爆了**
设置里把「计算设备」改成 `cpu`，或「量化精度」改成 `int8`；也可换 `large-v3-turbo`（1.5 GB，8GB 显存很稳）。

**Q：视频画面黑屏但字幕能编辑**
Qt 的解码器不认 H.265/10bit。字幕、时间轴、导出都不受影响，只是预览受限；
装个 HEVC 扩展或转成 H.264 即可。

**Q：大模型把整段改写了 / 翻译了**
说明提示词太松。保持「严格模式」开启，并在术语表里补上专有名词；
必要时把「每批行数」调小（如 20），模型注意力更集中。

**Q：API Key 存在哪？**
`%APPDATA%\SubtitleStudio\config.json`（该目录不可写时自动改用程序目录下的 `SSData\`）。
纯本地文件，不上传。

想完全绿色便携（配置和数据都跟着程序走），设一个环境变量即可：

```bat
set SUBTITLE_STUDIO_HOME=D:\我的字幕工坊数据
```

配置、缓存、模型下载目录会全部放进那儿（测试套件也用它做隔离）。

**Q：模型名怎么填？**
填服务商控制台里显示的那个 ID（如 `deepseek-chat`、`kimi-latest`、`qwen-plus`）。
Ollama / LM Studio 等本地服务的 Base URL 用 `http://127.0.0.1:11434/v1`、
`http://127.0.0.1:1234/v1`，API Key 随便填。

---

## 九、版本系统与打包

### 版本号只有一个真源：根目录 `VERSION`

```
VERSION          ← 改版本就改这一行，例如 1.0.0 → 1.1.0
CHANGELOG.md     ← 发版时自动插入新版本段落，记得补发布说明
release.py       ← 一条命令完成：改版本 → 写 CHANGELOG → 提交 → 打 tag → 打包
```

版本号会自动流到四处，永不脱节：

| 出现位置 | 来源 |
|---|---|
| 窗口标题 `… 视频字幕工坊 v1.0.0` | `sstudio/version.py` |
| `--version` / `--check` 输出 | 同上 |
| exe 右键·属性·文件版本 `1.0.0.0` | `build.spec` 生成版本资源 |
| 打包产物内的 `BUILDINFO` | 构建时的 git 短号 + 是否脏工作区 |

### 发版

```bat
python release.py                          :: 交互式，问下一个版本号
python release.py 1.1.0 -m "新增批量导出"   :: 直接发 1.1.0，写 CHANGELOG + 打 tag
python release.py 1.1.0 --build            :: 发版并打包
python release.py --build                  :: 版本不变，只重新打包
python release.py 2.0.0 --dry-run          :: 只演练，什么都不写
```

脚本会先检查两件事，任一不满足就停下而不是硬来：git 提交身份未配置、工作区有未提交改动（避免把无关改动混进发版提交）。tag 形如 `v1.1.0`；脚本不会自动 push，需要时自己 `git push && git push origin v1.1.0`。

语义化版本约定：不兼容改动进 MAJOR，加功能进 MINOR，修 bug 进 PATCH。预发布写 `1.1.0-rc.1`，会被标成"（预发布）"。

### 打包

```bat
pip install pyinstaller
pyinstaller build.spec --noconfirm
:: 产物：dist\Subtitle Studio\Subtitle Studio.exe   （约 341 MB / 370 个文件）
```

采用 **onedir**（一个文件夹）而非 onefile：faster-whisper 依赖的 ctranslate2、onnxruntime 体积大，onefile 每次启动都要解压到临时目录，冷启动慢十几秒，且更易被杀软误报。

打包时值得注意的三点，都是踩过坑的：

* **模型不打包**。每个 1.5–3 GB，且你本机已有。程序运行时扫描 `D:\VideoCaptioner\AppData\models` 等位置直接复用，见第三节。
* **`PyQt5.QtXml` 不能排除**。qfluentwidgets 硬依赖它，只有 0.2 MB；误排会让打包版界面直接起不来（自检里会显示 `No module named 'PyQt5.QtXml'`）。
* **git 调用带 `CREATE_NO_WINDOW`**。打包版是无窗口程序，起子进程会闪黑框；冻结环境直接跳过 git，改读构建期固化的 `BUILDINFO`。

### 验证打包产物

```bat
"dist\Subtitle Studio\Subtitle Studio.exe" --version
"dist\Subtitle Studio\Subtitle Studio.exe" --check
```

`--check` 会逐项打印 PyQt5 / qfluentwidgets / faster-whisper / CTranslate2 / GPU 可推理 / ffmpeg，并列出发现的本地模型。GUI 版用户看不到命令行，遇到"打不开"时把 exe 拖到 cmd 里跑这两条即可定位。

> 提醒：配置与 API Key 写在 exe 同级的 `SSData\`，是明文。拷给别人前记得清空这个目录（或首次启动后在设置里填）。

---

## 十、技术选型

* **PyQt5 + PyQt-Fluent-Widgets** — 原生桌面体验，Win11 风格，离线可用。
* **faster-whisper (CTranslate2)** — 比原版 openai-whisper 快 4 倍、显存更省，支持词级时间戳。
* **OpenAI SDK** — 只用 `chat.completions` 这一层通用能力，兼容几乎所有国内外服务商与本地推理。
* **不引入字幕解析三方库** — SRT/VTT/ASS 的解析自己实现，容错更好（能读脏文件），也少一层依赖。
