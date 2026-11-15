# 打包准备与启动退出

## 当前入口

`ptcglens.py` 无参数默认进入原生双语浮卡模式，兼容原 `--overlay`。源码环境先执行 `uv sync`，随后可以运行 `uv run ptcglens.py`，或双击项目根目录的 `Start-PTCGLens.cmd`（Windows）／`Start-PTCGLens.command`（Mac）。启动器使用项目自己的虚拟环境，可从其他工作目录启动；Mac 源码启动器仍通过终端执行。

启动器不是独立 exe/app。未来发布包直接使用 `ptcglens.py` 入口，无需用户传 `--overlay`。源码网页总览明确使用 `--web`；`--port`、`--capture-mode` 需搭配 `--web`，不会在浮卡模式中被默默忽略。冻结后的应用暂不提供网页子进程模式，防止把冻结程序自身当 Python 解释器递归启动。

`--check-permissions` 只检查权限，不启动采集；默认检查浮卡所需输入权限，搭配 `--web` 时只检查采集权限。`--quit` 单独使用，不请求屏幕或输入权限。

## 资源与数据

`app_paths.py` 统一提供路径，导入模块不会创建文件。只读资源由源码目录或 PyInstaller 的 `_MEIPASS` 目录加载，不依赖当前工作目录。运行数据默认放在：

- Windows：`%LOCALAPPDATA%\PTCGLens`，缺少该环境变量时回退到用户的 `AppData\Local\PTCGLens`。
- Mac：`~/Library/Application Support/PTCGLens`。

目录中保留 `overlay-settings.json`、`translation-cache.json`、`card-data.json`、`index/`（含紧凑特征缓存）、`card-thumbnails/`、`card-sources/`、`card-large/`、`query-failures/` 和 `logs/overlay.log`。卡图仍来自当前用户的游戏缓存和既有在线来源，发布包不附带本机游戏素材。单份日志约 2 MiB，保留最多 3 份备份；无控制台启动也会写入状态和启动错误日志。

建索引、采集、网页等运行命令的默认数据路径与浮卡一致；显式路径参数继续有效。`query_regression.py` 和 `stability_check.py` 使用新目录中的识别资料，开发回放样本和显式报告仍保存在项目 `output/`。

可通过绝对路径环境变量 `PTCGLENS_DATA_DIR` 隔离测试配置。单实例锁和控制入口按此目录区分；同一个实例的再次启动或 `--quit` 必须使用相同目录。切换该变量不会搬迁原用户数据目录；源码版首次仍可复制旧 `output/` 中的指定数据。

从旧源码版升级前先退出旧程序。新浮卡启动时复制旧 `output/` 中的设置和译文，在后台复制指定索引与卡图缓存，保留时间戳以复用已有特征；不删除原文件，不覆盖目标已有内容，不导入历史日志、截图或进程锁。按整个文件／子目录暂存后发布，复制失败不会留下半份已发布索引。缓存导入有完成标记，关闭程序时可在步骤间取消；已执行中的单次复制或原生计算仍需等待返回。已有目录不会合并覆盖，可重建缓存缺失时由正常准备流程重建。

源码版可继续读取原 `.tmp/ptcg-live-zh-mod/databases_zh-CN` 对照表，同时将缺失的三份对照表复制到用户目录供未来独立应用使用。发布包默认只读用户数据目录中的可选 `translations/databases_zh-CN`；没有上游表时继续使用自带参考译文与自动翻译。

## 可见入口与退出

每次启动显示“运行状态与准备”窗口，提供数据／日志目录按钮和“退出 PTCGLens”。有托盘时，关闭该窗口或选择“返回后台”只隐藏管理窗口；再次启动同一程序会请求已有实例显示状态窗口，不启动另一套采集和输入线程。

Windows 在系统托盘显示图标，可能在“隐藏的图标”中；Mac 在顶部菜单栏显示图标，不显示 Dock 图标。菜单均保留设置、运行状态、关闭浮卡及退出。Windows 点击托盘图标也可打开状态窗口。托盘不可用时，管理窗口作为退出入口，关闭窗口就退出；运行中托盘从可用变为不可用时会重新显示管理窗口。

Esc、关闭浮卡、查询快捷键仅取消当前查询。退出程序使用菜单或管理窗口的“退出 PTCGLens”，也可运行 `uv run ptcglens.py --quit`（独立包为 `PTCGLens.exe --quit` 或 app 内的可执行文件加 `--quit`）。命令成功表示退出请求被接收，不表示后台清理已同步完成。

本地控制采用 Qt 本地 socket／命名管道，不监听 TCP 端口，限制为当前用户访问。退出会先取消查询和迟到结果，隐藏浮卡与管理窗口，停止计时器、输入钩子和后台监测，通知识别与刷新线程停止，并有界等待；释放本地控制服务和单实例锁。退出清理可重复调用，不重复关闭资源。

## Mac 本机测试包

已增加 PyInstaller 构建脚本、菜单栏辅助应用元数据、图标、独立证书和第三方许可。Mac 测试包使用本机 ad-hoc 签名，尚未做 Developer ID 签名或 Apple 公证。安装与构建说明见 [Mac 测试包](macos-test-package.md)。

Windows x64 便携测试包已于 2026-11-15 构建，包含无控制台 EXE、运行时、自检和退出入口；中文路径、清理 Python 环境变量后的独立自检、重复启动、正常退出、移动后的设置／缓存保留，以及程序目录文件不变检查通过。构建会排除宿主开发工具的 DLL 搜索路径。具体证据和未测边界见 [Windows 测试包](windows-test-package.md)。

## 后续发布包工作

正式分发仍需签名、公证和干净机器验收。构建应分别在 Windows 和 Mac 上进行，先使用 PyInstaller 的文件夹形式，Mac 输出 `.app`。除 Python 模块及原生库，还必须包含 `battle_layout.sample.json`、`card-image-rules.json` 和 `translations/local-zh.json`；如果将来扩展冻结包的网页模式，还需收集 `viewer/` 并改造服务入口。

构建还需检查 Qt 平台／图像插件、OpenCV、UnityPy 与所选平台的动态后端和原生绑定。Mac 固定 bundle ID 并设置菜单栏辅助应用属性，验证签名后屏幕录制、辅助功能和输入监控的授权归属；分别确认 Apple Silicon／Intel 目标架构。Windows 验证无控制台启动、托盘、中文用户路径、安装目录只读及原生采集组件。

发布包必须在没有 Python 的干净环境做启动、权限、查询、重复启动、退出和升级保留数据测试。现有本机冻结包自检不能替代这些检查。跨平台功能已按用户授权合入 main；Windows 源码版完成了一轮用户人工验收，Windows 冻结包的游戏内实测仍待进行。
