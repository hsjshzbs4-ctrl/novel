# 安装（Windows）

> 目前只支持 Windows。macOS / Linux 未验证，安装脚本已移除——不做未经验证的支持。

## 需要什么

- **Windows 10 / 11**
- **Python 3.10+**：[python.org 下载](https://www.python.org/downloads/)（安装时勾选 **Add python.exe to PATH**）
- 一个 **DeepSeek API Key**（[platform.deepseek.com](https://platform.deepseek.com)）
- 不需要 GPU，不需要本地模型

## 安装

**方式一：双击（最简单）**

下载仓库后，双击 `scripts\install.cmd`。它会用 PowerShell 跑安装脚本，装完停住让你看结果。

**方式二：PowerShell**

```powershell
git clone <仓库地址>
cd novel
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

**方式三：手动**

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install .
novel --help
```

脚本会优先用 `pipx` 装成全局 `novel` 命令；没有 pipx 就在项目里建 `.venv`。

**要导出 docx 需要额外装一个依赖：**

```powershell
pip install "novel-engine[export]"
# 或直接
pip install python-docx
```

epub / txt / md 不需要额外依赖。

## 配置 Key

在**作品目录**（不是安装目录）建 `.env`：

```bash
NOVEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

可选：

```bash
NOVEL_MODEL_WRITER=deepseek-flash   # 换模型
NOVEL_CONTEXT_BUDGET=6000           # 单章上下文预算
NOVEL_REVIEW=0                      # 关掉评审（省钱、快一倍）
NOVEL_HISTORY_RECENT=30             # 版本历史保留
```

`novel doctor` 会打印当前的 provider、模型路由和 key 是否就位。

## 验证

```powershell
novel doctor
novel init 试写 --genre 玄幻 --chapters 24 --words 900 --path .\demo
novel architect --idea "被逐出师门的少年重返宗门" --path .\demo
novel plan --count 2 --path .\demo
novel write --ch 1 --path .\demo
```

跑通后 `.\demo\chapters\ch-001.md` 里应该有正文。

## 常见问题

**提示"无法加载文件，因为在此系统上禁止运行脚本"**

PowerShell 执行策略拦住了脚本。用我们提供的 `install.cmd` 双击运行，它会自动带上 `-ExecutionPolicy Bypass`；或者手动加这个参数。

**`python` 不是内部或外部命令**

Python 没进 PATH。重装时勾选 "Add python.exe to PATH"，或把 Python 目录手动加进环境变量。

**pip 卡住不动**

两种情况：

1. 默认临时目录不可用。安装脚本已经把 `TEMP`/`TMP` 指到项目内 `.tmp`；手动安装请自己设：
   ```powershell
   $env:TEMP="<项目>\.tmp"; $env:TMP="<项目>\.tmp"; pip install .
   ```
2. 默认 PyPI 网络慢。换镜像（安装脚本也会遵循这个环境变量）：
   ```powershell
   $env:PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
   ```

**控制台中文变乱码**

引擎已经把输出流强制成 UTF-8 / `replace`，正常不会崩。如果仍有乱码，执行：

```powershell
chcp 65001
```

**模型报 400：不支持的模型名**

模型名以接口返回为准：

```powershell
curl.exe https://api.deepseek.com/models -H "Authorization: Bearer $env:DEEPSEEK_API_KEY"
```

**安装后 `novel` 命令找不到**

用 `.venv` 安装时命令在 `.venv\Scripts\novel.exe`；想全局可用就装 `pipx`（`python -m pip install --user pipx`）后重跑安装脚本。

## 卸载

```powershell
pipx uninstall novel-engine     # pipx 装的
# 或
Remove-Item .venv -Recurse -Force   # venv 装的
```

作品数据在各自的 `novel.db` 与 `chapters\` 里，卸载命令不会删。
