# Windows x64 便携测试包

当前发布准备版为 **v0.1.0-alpha.1**，统一版号和双平台构建规则见[版本约定](versioning.md)。下方 2026-11-15 的 `0.1.0` 时间戳包是首轮本机打包验证记录，不能替代 alpha.1 的同提交 CI 构建证据。

## 使用

解压 ZIP 后双击 `PTCGLens/PTCGLens.exe`。整个 `_internal` 目录必须保留，无需安装 Python、uv 或开发工具。默认入口是悬停浮卡；包内不提供开发用网页模式。包内 `使用说明.txt` 说明快捷键、托盘、退出和数据目录。

“便携”指免安装运行；设置与缓存仍保存在 `%LOCALAPPDATA%\PTCGLens`。升级前退出旧程序，将新包解压到另一目录后运行，已有用户数据会继续使用。不要覆盖正在运行的 EXE/DLL。

自检可双击 `Self-Check.cmd`，或在 PowerShell 执行：

```powershell
& '.\PTCGLens.exe' --self-check "$env:LOCALAPPDATA\PTCGLens\diagnostics\portable-self-check.json"
& '.\PTCGLens.exe' --quit
```

自检不采集屏幕；报告中的 `ok` 为全部检查的汇总。运行日志为用户目录下的 `logs/overlay.log`。`--quit` 请求被接收后，还需等待进程正常退出和锁释放。

## 构建

在 Windows x64 的 PowerShell 中、项目根目录执行：

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$PWD\.tmp\windows-build-env"
uv sync --locked --group build --python 3.13.11
& '.\.tmp\windows-build-env\Scripts\python.exe' -X utf8 scripts/build_windows.py
```

依赖来自现有 `uv.lock` 的可选 `build` 组。脚本仅接受 Windows x64 Python，生成无控制台 EXE、图标、版本资源、构建信息、项目和第三方许可、使用说明与自检／退出批处理。构建中间文件在 `build/`，交付物为：

- `dist/PTCGLens-<公开版本>-windows-x64.zip`
- 同名解压目录和 `.zip.sha256` 校验文件
- `dist/windows-latest-build.json`：最新 ZIP、EXE、SHA-256 与大小

构建时限制 DLL 搜索路径为构建 Python 环境及 Windows 系统目录，拒绝带入其他宿主工具的 DLL。包内收集 UnityPy、archspec CPU 资料、FMOD、Windows Capture 原生扩展、Qt 和证书；保留用户显式指定的证书配置。资源目录不会产生 Python 字节码缓存。

包内不附带游戏缓存、截图、个人设置、译文缓存、上游中文对照表或开发目录。测试产物不提交 Git。

## 2026-11-15 验证结果

最终测试 ZIP 为 `PTCGLens-0.1.0-Windows-x64-test-20261115-233524.zip`，114,711,856 字节（约 109.4 MiB）。SHA-256：

```text
480a8b2d3bedc429c232ef0efe20dcd69f31c0760f8fd1fbd2bee6ad4302d06e
```

构建使用 Python 3.13.11、PyInstaller 6.22.3、PySide6 6.11.2，基于 `83ba5ea` 加本轮打包改动；包内 `build-info.json` 保存源码摘要和具体依赖版本。

| 检查 | 结果与范围 |
| --- | --- |
| 源码完整测试 | 235 项运行，219 项通过、16 项平台相关跳过；网页双语测试通过 |
| ZIP 与 EXE | 校验值及 ZIP 完整性通过；AMD64、Windows GUI 子系统；未混入用户运行数据 |
| 任意目录／中文路径 | 在带中文及空格的解压路径、独立空工作目录运行，PATH 仅保留 Windows 系统目录，移除 Python 环境变量 |
| 包内自检 | 六项通过：静态资源、OpenCV/SIFT 与中文路径图片读写、真实 Unity 卡图解码、Qt 离屏渲染、Windows 原生后端及采集扩展导入、HTTPS 证书 |
| 真实游戏缓存样本 | 只读复制索引清单；解码本机缓存 `bw10_en_090` 为 256×256×3，不采集游戏画面 |
| 正常 Windows 界面后端 | 日志确认 `Qt 平台：windows`，并进入无缓存的准备等待状态；不是只测 offscreen |
| 重复启动与退出 | 重复启动返回 0 且锁归属 PID 不变；`--quit` 后进程返回 0、锁释放，退出清理日志完整 |
| 移动后重启与用户数据 | 退出后在本次验证目录内移动解压目录；重新启动、重复启动和退出通过，`Ctrl+Alt+Q`、1 秒悬停及隔离译文缓存逐字节保留 |
| 安装目录写入 | 自检和两轮启动退出前后，程序目录所有文件及 SHA-256 完全一致；未做 NTFS ACL 强制只读测试 |

原始报告在本机忽略目录 `output/windows-portable-verification-20261115-233559/verification.json`，自检详细报告为同目录 `frozen-self-check.json`；构建日志为 `output/windows-portable-build-fixed.log`。验证脚本可复用：

```powershell
$build = Get-Content 'dist/windows-latest-build.json' -Raw | ConvertFrom-Json
& '.\.tmp\windows-build-env\Scripts\python.exe' -X utf8 scripts/verify_windows_package.py $build.archive `
  --source-data "$env:LOCALAPPDATA\PTCGLens"
```

脚本只读来源数据的索引清单，用户设置和译文使用独立测试样本；启动时指定空游戏缓存，使程序停在准备阶段，不注册输入钩子、不采集游戏画面。验证仅使用命令行、进程退出码、命名管道、日志与文件哈希，没有启用 Codex 桌面监控。

首轮冻结暴露宿主 PATH 中的 Poppler ICU 被误带入，导致 Qt 缺少 DLL 入口点；清理构建环境并加入二进制来源检查后，上述 ZIP 通过全部检查。失败产物移至本机 `build/windows-failed-artifacts/`，不交付。

## 验证边界

这是未代码签名的本机测试包，尚未发布。没有使用另一台真正未安装 Python 的干净 Windows 机器，也未在旧版 Windows 上验收。

本轮独立 EXE 未进行游戏内浮卡、快捷键和长时间游戏的人工复测；此前源码版本的用户验收不能扩大为冻结包验收。离线回退按用户要求跳过，多显示器和更多系统缩放仍保留待测。证书自检通过不代表在线翻译服务调用已成功。
