# Mac 本机测试包

当前发布准备版为 **v0.1.0-alpha.1**，适用于 Apple Silicon（M 系列）Mac、macOS 14 及以上。自带 Python、Qt、识别与平台依赖，不需要安装 Python 或 uv；没有 Intel 包。Windows x64 与 Mac arm64 从同一提交构建，规则见[版本约定](versioning.md)。下方 2026-11-01 的 `0.1.0` 归档是历史本机测试包，不能改名作为本次发布包。

## 安装与使用

1. 解压 `PTCGLens-0.1.0-alpha.1-macos-arm64.zip`，将 `PTCGLens.app` 放到用户的“应用程序”目录中。先退出旧源码版浮卡，再双击启动此应用。
2. 首次运行按提示在“系统设置 → 隐私与安全性”中，为 **PTCGLens 本身**开启屏幕录制（新系统可能叫“屏幕与系统音频录制”）、辅助功能和输入监控，然后退出并重新打开应用。原先对终端或 Codex 的授权不代表新 app 已获授权。
3. 进入游戏，按一下 **Control+Option** 开启查询，悬停卡牌；再次按下关闭。缺失的中文仍自动翻译，英文保留。
4. 整个程序的退出入口在顶部菜单栏图标和“运行状态与准备”窗口中，名称都是“退出 PTCGLens”。关闭浮卡或 Esc 只关闭查询。

测试包采用本机 ad-hoc 签名，没有 Developer ID 签名和 Apple 公证，主要供本机验证。若系统阻止启动，请保留提示供排查，不要关闭系统安全保护。

设置、索引和卡图仍保存在 `~/Library/Application Support/PTCGLens`；已有数据可复用。退出、替换 app 不会删除这些数据。日志在该目录的 `logs/overlay.log`，可从菜单或状态窗口打开。反馈异常时记录卡名、窗口／全屏模式和发生时间，方便与日志对应。

## 从源码重新构建

```sh
uv sync --locked --group build
uv run --locked --group build python scripts/build_macos.py
```

打包依赖属于可选的 `build` 组，不改变普通运行所需的依赖版本。构建脚本在本机临时目录完成 `.app` 和签名，再把保留符号链接的 ZIP 及 SHA-256 校验文件写入 `dist/`，临时应用会自动清理；`build/` 和 `dist/` 不提交到 Git。

构建使用已安装的独立 Command Line Tools（存在时），不切换系统 Xcode、不自动接受许可。本机曾出现旧 Qt 安装目录中的异常重复／隐藏文件，必要时可用绝对路径 `UV_PROJECT_ENVIRONMENT=/private/tmp/ptcglens-macos-build` 配合上述两条命令建立干净构建环境。应用在临时目录签名，避免 Documents 等同步目录给 framework 添加 Finder 元数据。签名前只移除构建产物的 FinderInfo／ResourceFork，不清除隔离等安全属性。

包内含动态加载的 Mac 后端、PyObjC 框架绑定、UnityPy 类型资源、archspec CPU 资料、FMOD 动态库和 HTTPS 根证书。用户显式指定的 `SSL_CERT_FILE` 仍优先；证书校验保持开启。包内的第三方许可位于 `Contents/Resources/third-party-licenses`，本项目 GPL 许可也随包提供。包内不含本机游戏截图、游戏缓存或私人配置。

## 自检与边界

解压后的应用可执行以下命令生成诊断报告，不采集画面、不请求权限：

```sh
"/实际位置/PTCGLens.app/Contents/MacOS/PTCGLens" --self-check /tmp/ptcglens-check.json
```

检查只读资源、OpenCV/SIFT、Qt 离屏中文渲染、原生后端、游戏缓存存在时的纹理解码和 HTTPS 证书。可加 `--check-network` 用固定英文短句验证在线翻译；服务限流等网络错误会单独记录，不能把证书通过等同于在线翻译成功。启动权限提示期间也支持 `--quit`，不需要授予采集权限才能退出。

自检不能代替真实窗口／全屏、游戏输入、系统权限和长期对局测试。此次仍保留 Windows 游戏实机、Intel、较低 macOS 版本和真正未安装 Python 的另一台机器验证待办。
