# 发布

面向维护者。用户安装看 [install.md](install.md)。

## 打本地包

```bash
pip install build hatchling
python -m build --no-isolation --wheel --sdist
```

产物在 `dist/`：

- `novel_engine-0.1.0-py3-none-any.whl` —— 给别人 `pip install` 用
- `novel_engine-0.1.0.tar.gz` —— 源码分发，含 `skills/`、`docs/`、`bench/`、`tests/`

> 不加 `--no-isolation` 时 build 会临时装 hatchling；网络慢会卡很久。

## 发到 PyPI

```bash
pip install twine
twine check dist/*
twine upload dist/*          # 需要 PyPI 账号和 API token
```

名字 `novel-engine` 如果已被占用，改 `pyproject.toml` 里的 `name`（命令名 `novel` 不受影响）。

## 发到 GitHub

打 tag 并把 wheel 挂到 Release：

```bash
git tag v0.1.0
git push origin v0.1.0
```

别人就能这样装：

```bash
pipx install git+https://github.com/hsjshzbs4-ctrl/novel
```

## 发布 Skill 外壳

`skills/novel-engine/SKILL.md` 是给 Claude Code / Codex 这类宿主用的说明书。分发方式有两种：

1. **跟着仓库走**：用户 clone 后，把 `skills/novel-engine/` 复制到宿主的 skills 目录；
2. **用 skills 安装器**：仓库根目录保持 `skills/<name>/SKILL.md` 结构即可被 `npx skills add <repo>` 识别。

Skill 只负责“教 Agent 怎么用命令”，真正的能力在 CLI 里——所以两者要一起发。

## 发布前检查

```powershell
python -m pytest                 # 全绿
python bench/scale.py            # 千章规模不退化
python -m build --no-isolation   # 打包无警告
```

再在一个干净虚拟环境里装一次 wheel：

```powershell
python -m venv .tmp\pkgtest
.\.tmp\pkgtest\Scripts\pip install (Get-ChildItem dist\*.whl).FullName
.\.tmp\pkgtest\Scripts\novel.exe --help
```

再跑一次安装脚本本身（最容易出问题的一环）：

```powershell
# 复制一份干净副本，避免污染开发环境
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
.\.venv\Scripts\novel.exe --help
```

> **`scripts/install.ps1` 和 `install.cmd` 必须保持纯 ASCII。** PowerShell 5.1 会把无 BOM 的 UTF-8
> 当 ANSI 读，中文字节可能吞掉换行符，导致下一行代码被并进注释——脚本静默失效，
> 而且只在部分机器上复现。发布前用「非 ASCII 字节数 = 0」检查一遍。

## 支持范围

目前**只支持 Windows**：

- `pyproject.toml` 的 classifier 只声明 `Operating System :: Microsoft :: Windows`；
- 不提供 macOS / Linux 安装脚本（曾经写过一个，未实测，已移除）；
- 引擎代码本身是跨平台的（路径用 `as_posix()`，控制台强制 UTF-8），将来要做移植，
  缺的是实测而不是代码。
