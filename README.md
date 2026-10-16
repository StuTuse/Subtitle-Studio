# Subtitle Studio · 视频字幕工坊

**开源许可：[GPL-3.0-only](LICENSE)**，适用范围见 [许可说明](COPYING.md)；第三方组件保留各自许可，见 [第三方清单与分发待办](THIRD_PARTY_NOTICES.md)。

作者：**TuseCreation** · GitHub：[StuTuse](https://github.com/StuTuse)。

**Code signing policy**：[签名政策](CODE_SIGNING.md) · [隐私政策](PRIVACY.md)。目前正在准备 SignPath Foundation 申请，尚未获批，现有安装包仍未签名；本地准备不代表已经公开源码或发布新安装包。

[当前下载及签名状态](DOWNLOADS.md)区分历史公开安装包和本地准备的新版本。

导入视频 → **本地 Whisper 转写** → **大模型只改错别字** → 导出 SRT / VTT / ASS / TXT / JSON …

一条为「UP 主 / 剪辑师 / 课程制作者」设计的字幕生产线：转写在本地跑（不上传隐私内容），
改错别字交给大模型（但严格禁止它改写句式），最后你自己逐条过一遍就能出片。

> **语言说明**：本软件以**中文优先**——界面文案、文档、日志、提示均以中文为准；
> 英文界面（en）为渐进式覆盖（约部分双语串），**不承诺完整翻译**，未覆盖处将显示中文。
> 这是有意的定位选择，非缺陷。

**下载安装包（Windows）**：到 [Releases 最新版](https://github.com/StuTuse/Subtitle-Studio/releases/latest)
下载 `SubtitleStudio-x.y.z-setup.exe`（约 84 MB，用户级安装、免管理员）。
装完即用，模型在首次转写时自动下载。

**遇到问题 / 想提功能**：[ Issues 页](https://github.com/StuTuse/Subtitle-Studio/issues)发帖；
应用内 **设置 → 环境体检 → 打开体检窗口 →「复制诊断」** 可一键复制版本与组件摘要
（不含 API Key 等敏感信息），贴进 issue 能省一轮往返。

---

## 一、它能做什么

| 环节 | 能力 |
|---|---|
| **导入** | 拖拽或选择 mp4/mkv/mov/m4a/mp3… 任意媒体；也可直接导入已有字幕（srt/vtt/ass/lrc/txt/json/md/html） |
| **转写** | faster-whisper（本地 GPU/CPU）、whisper.cpp、云端 Whisper API 三种后端 |
| **纠错** | OpenAI 兼容接口（DeepSeek / Kimi / 通义 / 智谱 / 豆包 / OpenRouter / Ollama / LM Studio…）<br>可写提示词、贴**原始稿件**、维护术语表；只改错别字，绝不动时间轴 |
| **精修** | 播放器 + 时间轴 + 字幕表三向联动，逐条编辑 / 拆分 / 合并 / 平移 / A-B 循环 / 变速 / 撤销重做 |
| **导出** | SRT、VTT、ASS、TXT（纯稿件 / 带时间戳）、JSON（含词级时间戳）、Markdown、HTML、LRC，可批量多选 |
| **其它** | 工程文件 `.ssp` 保存全部状态（原文/改后/词级时间戳）、自动保存、导出前体检、无界面批处理 |

---

## 二、快速开始（打包版用户）

1. 到 [Releases](https://github.com/StuTuse/Subtitle-Studio/releases/latest) 下载安装包并安装。
2. 启动后跟着**首次体检向导**走：缺什么组件点一下自动补装。
3. **设置**页 → 选模型预设（DeepSeek/Kimi/通义…）→ 填 API Key → **测试连接**。
4. **字幕编辑**页 → 拖入视频 → **转写**（首次自动下载模型）→ **AI 纠错** → 导出成品。

## 二·五、快速开始（源码开发）

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
%USERPROFILE%\.cache\huggingface\hub
%USERPROFILE%\.cache\modelscope\hub
%USERPROFILE%\.cache\whisper
%LOCALAPPDATA%\SubtitleStudio\models                   ← 本程序自己的下载目录
```

在 **设置 → 语音转写引擎** 里点「重新扫描本地模型」即可看到全部命中项；
也可以在「模型」框里直接粘贴任意 CTranslate2 模型目录的完整路径。

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
4. 标准 CUDA 安装目录与 PATH
```

找不到时会自动降级用 CPU 跑完，并在进度条上说明原因，不会中途崩掉。
想用回 GPU，任选其一：

```bat
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 -i https://pypi.tuna.tsinghua.edu.cn/simple
```

或在设置里点「探测 CUDA」，把目录填成任意 CUDA 12 的 `cublas64_12.dll` 所在目录。

---

## 三·五、实测数据

| 项目 | 结果 |
|---|---|
| 素材 | 8 分 29 秒中文口播配音 |
| 模型 | `faster-whisper-large-v3-turbo`（本地已有模型，0 下载） |
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
   │  ├─ transcriber.py       三种转写后端 + 本地模型自动发现
   │  ├─ cuda_rt.py           自动定位 CUDA 12 运行库（复用已有的 cublas64_12）
   │  ├─ llm.py               提示词组装、编号往返解析、严格校验、并发批次
   │  ├─ config.py            配置持久化（含便携模式回退）
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
设置 → 模型 → 重新扫描本地模型 → 选一个已下载的模型；或联网状态下选「在线下载」项，首次自动下到 `%LOCALAPPDATA%\SubtitleStudio\models`。

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

## 八·五、隐私说明（本程序采集什么）

完整说明见 [隐私政策](PRIVACY.md)。本地转写在本机执行；选择云端转写会发送音频，启动远程大模型纠错会发送字幕及任务上下文。

| 数据 | 去向 | 说明 |
|---|---|---|
| 视频/音频文件 | 本机或你选择的云端转写服务 | 本地引擎在本机识别；主动使用云端转写时，会向所配置服务发送音频 |
| 字幕内容 | 只在你本机 + 你配置的大模型服务商 | 只有你主动点「开始纠错」时，才把**字幕文本**发给你在设置里填的模型服务商（Base URL 可见，可用本地 Ollama/LM Studio 实现全离线） |
| API Key | 本机保存；请求时用于所选服务的鉴权 | Windows 上经 DPAPI 保护配置；不会作为遥测发送给维护者，配置备份仍需妥善保管 |
| 转写模型 | 从网络下载到本机 | 默认走 ModelScope 的 `pengzhendong/faster-whisper-*` 公开仓库（阿里云托管），下载完成后逐文件 SHA-256 校验，不符即拒用；也可手动指向本地已下载模型实现零联网 |
| 启动更新检查 | GitHub API 与下载代理（可关） | 默认开启，当前依次尝试 `gh-proxy.com`、`ghfast.top`、`api.github.com`；服务器可看到 IP 和请求头，不主动携带媒体、字幕或 API Key；可在 设置 → 软件界面 →「启动时检查新版本」关闭 |
| 崩溃报告 | 只在你本机 | 崩溃信息写本地 `crash.log` / `logs/app.log`，程序**不会自动上报**；反馈时由你决定是否附上 |

一键修复（体检窗「安装」按钮）会往你机器的全局 Python 装包，装前会弹窗列明将要安装/升级哪些包，确认后才执行。

> **模型档位说明**：转写模型共 tiny / base / small / medium / large-v3 / large-v3-turbo 六档，
> 体积 75 MB ~ 3 GB。tiny/base 被定位为「快速试用档」——首次想快速验证流程可用，
> 但中文识别质量明显弱，正式出片建议 small 起步、GPU 够用直接 large-v3-turbo。
> 软件默认档为 `large-v3-turbo`（8GB 显存实测最稳）。

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

* **模型不打包**。每个 1.5–3 GB。程序运行时扫描标准缓存目录（HuggingFace/ModelScope）与自家模型目录（`%LOCALAPPDATA%\SubtitleStudio\models`）直接复用，见第三节。
* **PySide6 双绑定防护**。v2.1.0 起 UI 栈是 PySide6（Qt6）+ PySide6-Fluent-Widgets；`build.spec` 把 PyQt5 整族排除——打包环境若残留两套 Qt 绑定，PyInstaller 检测到会中止构建，排除后构建环境再脏也能出干净包。
* **git 调用带 `CREATE_NO_WINDOW`**。打包版是无窗口程序，起子进程会闪黑框；冻结环境直接跳过 git，改读构建期固化的 `BUILDINFO`。

### 验证打包产物

```bat
"dist\Subtitle Studio\Subtitle Studio.exe" --version
"dist\Subtitle Studio\Subtitle Studio.exe" --check
```

`--check` 会逐项打印 PySide6 / PySide6-Fluent-Widgets / QtMultimedia / faster-whisper / CTranslate2 / GPU 可推理 / ffmpeg，并列出发现的本地模型。GUI 版用户看不到命令行，遇到"打不开"时把 exe 拖到 cmd 里跑这两条即可定位。

> 提醒：配置与 API Key 写在 `%APPDATA%\SubtitleStudio\config.json`，是明文。把这份配置拷给别人前记得清空该目录（或首次启动后在设置里重新填）。

---

## 九·五、换电脑继续开发

代码、tag、GitHub Release 都在 GitHub 上，**仓库本身不用拷**。要手工搬的只有三样：凭据、模型（其实也不用，见第 5 步）、可选的本地配置。

### 1. 新机器装什么

| 必装 | 说明 |
| --- | --- |
| Git for Windows | 克隆、发版打 tag |
| Python 3.10 x64 | 本机是 3.10.11，其他版本没验证过 |
| Visual Studio 2015–2022 可再发行组件（vc_redist.x64） | PySide6 / ctranslate2 缺 MSVC 运行时会在 import 时才炸，报错还看不懂 |

然后装依赖（国内走清华源）：

```bat
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install pyinstaller pyflakes -i https://pypi.tuna.tsinghua.edu.cn/simple
```

按需再装：

* **Inno Setup 6** — 发版脚本靠它出安装包。没装会温和跳过（只产出 `dist\`），脚本自动搜 `Program Files`、`Program Files (x86)`、`%LOCALAPPDATA%\Programs` 三处。
* **GitHub CLI** — 发布 Release 传附件用，没有就去网页手动传。
* **ffmpeg** — 可选。不在 PATH 时程序自动改用 PyAV 解码（`av` 随 requirements 一起装），不装也跑得通全流程。

> `torch` 不在依赖里，代码没有任何地方 import 它，别装（十几 GB 白搭）。

### 2. 拉代码、配提交身份

```bat
git clone git@github.com:StuTuse/Subtitle-Studio.git
cd Subtitle-Studio
git config user.name  "StuTuse"
git config user.email "StuTuse@users.noreply.github.com"
```

`release.py` 开工就先查提交身份，没配会直接停下而不是硬来。嫌 `.git` 大（452 MB，历史里躺着旧安装包二进制）可以 `git clone --depth 1`，但发版要打 tag，正式开发还是完整克隆。

### 3. 凭据（不进仓库）

```bat
gh auth login
```

发版脚本从 `%USERPROFILE%\.config\gh\hosts.yml` 读 `oauth_token`，`gh auth login` 之后自动可用。把旧机器那份 hosts.yml 整个拷过来也一样能用（里面是明文 PAT）。

LLM 的 API Key / Base URL 在 `%APPDATA%\SubtitleStudio\config.json`，明文——要么新机器设置页重填，要么整份配置拷过来。注意「恢复默认」不会清掉这两项（`Config._RESET_KEEP` 专门保住了它们）。

### 4. GPU 加速：不用装 CUDA Toolkit

运行时用的是 pip 装的 nvidia 官方 wheel：

```bat
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-runtime-cu12 -i https://pypi.tuna.tsinghua.edu.cn/simple
```

程序启动时会自己扫 `site-packages\nvidia\**\bin`，把 `cublas64_12.dll` 注册进 DLL 搜索路径（`sstudio/core/cuda_rt.py`）；真报 cublas 缺失，设置页的体检向导有一键修复。所以新机器**只要显卡驱动够新（支持 CUDA 12.9）就行，别装 CUDA Toolkit**。

### 5. 转写模型：什么都不用拷

1.5 GB 的模型 `.gitignore` 掉了。v1.12.0 起首次转写会自动从 ModelScope 下载（实测 18~23 MB/s，约 1 分钟，带进度和断点续传），落到 `%LOCALAPPDATA%\SubtitleStudio\models\`，之后永久复用。赶时间也可以直接拷这个目录，保持内部结构不变就能被自动发现。

### 6. 验证环境齐不齐

```bat
python tests\doctor.py              :: 体检：python/pyside6/faster_whisper/pyav/ffmpeg/cuda12
python -m compileall -q sstudio tests
set QT_QPA_PLATFORM=offscreen
python tests\run_all.py --quick     :: 不碰 GPU 的快速套件
python tests\run_all.py             :: 全量（含真实 GPU 转写，首次会触发模型下载）
```

`tests\transcribe.py` 用的测试素材是本机私人素材，新机器上不存在时该套件自己打印「跳过」，不算故障。

### 7. 顺带一提

`release_shortcut.py`（发版脚本的快捷方式同步测试）全在临时目录里跑，新机器直接就是绿的，不用拷旧快捷方式。真要用软件，跑一次 `release.py --build` 时它会自动把桌面快捷方式指到新的 `dist\` 产物。

### 8. 别拷的

`dist\`、`build\`、`installer\*.exe` 都是可再生的产物，几百 MB。

### 9. 迁移完自查一遍

```bat
python tests\doctor.py
python tests\run_all.py
python -X utf8 release.py --dry-run
```

三条都干净（体检无 required 缺失、全量测试通过、发版演练不报错），就可以在新机器上照常开发和发版了。

---

## 十、技术选型

* **PySide6（Qt6）+ PySide6-Fluent-Widgets** — 原生桌面体验，Win11 风格，离线可用；v2.1.0 起全仓单套 Qt 绑定（根除双绑定冲突，媒体链走 Qt6 内置 FFmpeg 后端，HEVC 原生可解）。
* **faster-whisper (CTranslate2)** — 比原版 openai-whisper 快 4 倍、显存更省，支持词级时间戳。
* **OpenAI SDK** — 只用 `chat.completions` 这一层通用能力，兼容几乎所有国内外服务商与本地推理。
* **不引入字幕解析三方库** — SRT/VTT/ASS 的解析自己实现，容错更好（能读脏文件），也少一层依赖。
