# 版号与测试发布约定

首个公开测试版为 **v0.1.0-alpha.1**。Windows x64 和 macOS arm64 共用同一版号、Git 提交及源码摘要。

`pyproject.toml` 是版本的唯一输入，使用 Python 的规范格式 `0.1.0a1`。`release_version.py` 映射公开版本和系统数字字段：

| 用途 | alpha.1 的值 |
| --- | --- |
| 项目版本 | `0.1.0a1` |
| 公开版本／包内构建信息 | `0.1.0-alpha.1` |
| Git 标签 | `v0.1.0-alpha.1` |
| Windows 数字文件版本 | `0.1.0.1001` |
| Windows ProductVersion | `0.1.0-alpha.1` |
| Mac CFBundleShortVersionString | `0.1.0` |
| Mac CFBundleVersion | `1001` |
| Mac PTCGLensReleaseVersion | `0.1.0-alpha.1` |

同一目标版本下，alpha.N、beta.N、rc.N 的系统构建序号分别为 1000+N、2000+N、3000+N，稳定版为 4000；N 为 1–999。测试修正递增 alpha.N，扩大测试后进入 beta.N；候选版使用 rc.N，首个稳定版为 0.1.0。稳定版修复递增修订号，新功能递增次版本号，不兼容的大改动才考虑主版本号。

公开发布后不覆盖标签或同版文件。有修改就发布新编号。构建时间、提交和依赖版本保存在 `build-info.json`，不把日期混入公开版号。

发行附件：

```text
PTCGLens-0.1.0-alpha.1-windows-x64.zip
PTCGLens-0.1.0-alpha.1-macos-arm64.zip
SHA256SUMS.txt
```

GitHub 构建流程只生成包和验证证据，不自动发布 Release。发布准备分支 `codex/release-*` 推送时触发双平台构建；分别运行完整源码测试和冻结包自检。两个包必须来自相同提交，并通过各自检查。先创建 `draft=true`、`prerelease=true` 的 Release 草稿；公开发布由用户另行授权。

使用命令行、构建日志、自检 JSON 和校验值验证；不启用 Codex 桌面监控。独立包游戏实战、Mac 首次授权、更多系统版本、干净机器与多显示器检查的边界随 Release 说明保留，不能用 CI 自检代替人工实测。
