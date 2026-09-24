# PTCGLens

PTCGLens 是一个实验性的 Pokémon TCG Live 卡牌识别工具。它从**本机游戏缓存**建立卡图索引，通过**前台游戏窗口的截图**识别卡牌，并在本机网页中展示卡图、英文资料和可用的简体中文对照。识别到的卡牌可以点击，打开便于阅读的中文大图。

程序只读游戏缓存和屏幕画面，不修改游戏文件，也不连接或注入游戏进程。本项目不是 Pokémon TCG Live 的官方工具。

## 目前能做什么

- 识别对局画面中预设位置的场上卡、手牌和弃牌堆顶卡，并跟随画面检测手牌位置。
- 定位放大的卡牌特写；牌组等非对局页面打开单张卡牌特写时也能识别。
- 监测游戏缓存新增的卡图，增量更新索引、缩略图及卡牌资料。
- 在本机网页中显示识别框、卡牌列表和中文阅读版大图；可切换查看游戏缓存中的原卡图。
- 对本地中文资料缺失的英文效果文本提供按需机器翻译，并缓存成功的译文。

**项目状态：原型。** 对局卡位以一张约 16:9 的截图标定，识别结果仍需结合原画面核对。具体边界见[已知限制](#已知限制)。

## 运行环境

- Windows；实时截取游戏窗口依赖 Win32 API。
- Python 3.12 已验证；需要安装 [requirements.txt](requirements.txt) 中的依赖。
- 本机安装过 Pokémon TCG Live，且游戏缓存里已有卡图和英文卡牌数据库。
- Git，用于获取单独维护的中文对照表。
- 浏览器，用于打开本机展示界面。

游戏目前无法联网时，可以先准备代码和 Python 环境；**首次建立索引仍需要本机已有的游戏缓存**。仓库不附带游戏卡图、数据库或翻译数据。

## 快速开始

以下命令在项目根目录的 PowerShell 中执行。

### 1. 安装 Python 依赖

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. 准备中文对照表

卡名和效果的本地中文对照来自 [PTCGL 中文化项目](https://github.com/Hill-98/ptcg-live-zh-mod)，需单独下载。只获取本项目使用的 `databases_zh-CN` 目录：

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
git clone --depth 1 --filter=blob:none --sparse `
  https://github.com/Hill-98/ptcg-live-zh-mod.git .tmp/ptcg-live-zh-mod
git -C .tmp/ptcg-live-zh-mod sparse-checkout set databases_zh-CN
```

### 3. 从游戏缓存建立数据

```powershell
.\.venv\Scripts\python.exe cache_watch.py --once
```

这一步建立 `output/index/` 特征索引、`output/card-thumbnails/` 缩略图和 `output/card-data.json` 卡牌资料。默认读取当前用户的游戏缓存；缓存位置与默认值不同时，可用 `--cache-root`、`--game-cache` 和 `--translation-root` 指定。命令状态保存在 `output/cache-watch-status.json`。

### 4. 启动实时界面

在**两个**项目根目录的 PowerShell 窗口中分别运行：

```powershell
.\.venv\Scripts\python.exe battle_live.py
```

```powershell
.\.venv\Scripts\python.exe viewer.py
```

打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。游戏在前台时程序才会更新截图；游戏未启动时识别进程会等待，重新打开游戏并切到前台后继续。网页服务默认只监听 `127.0.0.1`。在各自窗口按 `Ctrl+C` 可停止程序。

## 怎么使用

在对局画面中，网页会展示识别到的卡位。点击识别框或右侧卡牌列表，可打开中文阅读版大图，并切换至游戏缓存原卡图。在牌组等其他页面打开单张卡牌特写时，网页会单独识别并展示这张牌。

中文阅读版使用本地原卡插画与文字资料重新排版，**不是官方中文印刷卡面**。有本地简中对照时优先显示；缺少对照的效果文本保留英文，并出现“翻译”按钮。点击后才会将这段英文发送给 [MyMemory 翻译接口](https://mymemory.translated.net/doc/spec.php)，显示“机器翻译（仅供参考）”，并将成功结果缓存至 `output/translation-cache.json`。翻译需要联网；接口不可用时会提示错误，英文原文仍可阅读。屏幕截图和整张卡图不会发送给该接口。

网页画面只来自前台 PTCGL 窗口。切到别的程序后，截图会暂停，网页保留最后一帧并显示暂停状态。

## 其他命令

| 用途 | 命令 |
| --- | --- |
| 游戏运行时持续检查新增卡图 | `python cache_watch.py` |
| 从已有截图检测多个对局卡位 | `python battle_multi.py search --screenshot <截图路径> --adaptive-hand --adaptive-preview` |
| 搜索一张截图中的放大卡牌 | `python cache_search.py search --screenshot <截图路径>` |
| 使用热键抓取前台卡牌特写 | `python live_capture.py watch` |
| 列出可见窗口以排查游戏窗口标题 | `python live_capture.py windows` |

以上命令在虚拟环境未激活时，将 `python` 换成 `.\.venv\Scripts\python.exe`。热键模式中，鼠标位于前台游戏客户区时按 `Ctrl+Alt+L` 抓图，按 `Ctrl+Alt+Shift+Q` 退出。单帧识别使用 [battle_layout.sample.json](battle_layout.sample.json) 中的实验性卡位布局；结果写入 `output/`。

## 已知限制

- 只识别这台电脑已缓存的英文卡图；未下载的印次无法从本地索引匹配。
- 固定场上卡位按一张 1910×1075 的对局截图标定。手牌和放大特写可动态定位，但其他分辨率、桌面布局和动画效果仍需更多实测。
- 严重遮挡、背面朝上，或从未露出可辨识内容的牌无法可靠确认；动画中离开固定卡位的小卡也可能漏识别。
- 相似度和“待确认”状态不是经过校准的概率。不同印次共用卡图时，可能无法仅凭截图区分。
- 中文对照覆盖不完整。机器翻译可能误译卡牌专有名词或规则文本，不能代替原文核对。

## 数据与来源

`output/`、`.tmp/` 和本地依赖目录均被版本控制忽略。仓库不分发游戏截图、卡图、游戏数据库或中文化项目的数据。中文对照表由用户自行从 [PTCGL 中文化项目](https://github.com/Hill-98/ptcg-live-zh-mod)取得；该上游仓库标注为 GPL-3.0。卡牌及游戏素材的权利归其各自权利人所有。

## 许可证

本仓库源代码采用 [GNU GPL v3.0](LICENSE)（`GPL-3.0-only`）。游戏素材和单独下载的中文对照表不属于本仓库的授权范围。
