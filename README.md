# Enterprise Risk Information Research

企业风险信息理解实验：从企业公开披露文本提取主体、事件关系、条件和状态，核查中间结构对最终判断的影响。仓库保存实验代码、数据来源、固定设置和结果。

## 实验索引

|实验|设计与结果|结果文件|
|---|---|---|
|20261029：结构信息利用（GPU）|[实验说明](experiments/20261029-structure-probe/README.md)|[results.json](experiments/20261029-structure-probe/results.json)|
|20261029：段落完整性 × 任务规则|[实验说明](experiments/20261029-paragraph-control/README.md)|[results.json](experiments/20261029-paragraph-control/results.json)|

直接阅读上方的“实验说明”。JSON是程序记录；当前页面及实验说明是阅读入口。

本地查看已有结果：`.\.venv\Scripts\python.exe -m risk_text_pilot results`。在VS Code也可选择“终端 → 运行任务 → 查看已有结果（不运行模型）”。

默认打开最新的结构利用对照；查看旧段落实验时加 `--run-dir runs/20261029_paragraph_control_v1`。网页只查看已保存结果，不重新推理。

## 工程目录

```text
risk_text_pilot/   数据处理、推理与对照代码
configs/          数据来源、模型版本与配置模板
prompts/          版本化提示词
scripts/          本地模型服务脚本
tests/            离线单元测试
experiments/      实验设计与结果摘要
docs/             人工核验规范
python -m risk_text_pilot  统一操作入口
```

## 安装

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 -m risk_text_pilot test
.\.venv\Scripts\python.exe -X utf8 -m risk_text_pilot bootstrap --model-dir D:\AI-Models\risk-text-pilot
```

推理前需有已启动的本地服务，地址为 `http://127.0.0.1:18080/v1`，配置存于 `settings.local.json`。环境检查使用 `python -m risk_text_pilot doctor`。Windows服务管理脚本单独保留在 `scripts/serve.ps1`，在允许该脚本执行的环境中使用。日常数据处理、查看结果和测试均走Python入口。

## 准备数据

从 [数据来源](configs/data-sources.json) 下载PDF，保存为 `data/raw/CATL_2025_annual_report.pdf`，然后登记并提取文本：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m risk_text_pilot register --path data/raw/CATL_2025_annual_report.pdf --company "宁德时代新能源科技股份有限公司" --kind annual_report --url "https://static.cninfo.com.cn/finalpage/2026-03-10/1225002214.PDF" --date 2026-03-10
.\.venv\Scripts\python.exe -X utf8 -m risk_text_pilot ingest --page-start 14 --page-end 41
```

清单包含来源链接、SHA256和PDF物理页码；原文件保留在本机。

## 复现对照

```powershell
.\.venv\Scripts\python.exe -X utf8 -m risk_text_pilot control prepare --out runs/20261029_paragraph_control_v2
.\.venv\Scripts\python.exe -X utf8 -u -m risk_text_pilot control run --out runs/20261029_paragraph_control_v2
```

输出目录包含输入、提示词及模型清单、原始回答、校验结果和 `comparison.html`。使用浏览器阅读HTML；JSON/JSONL用于程序核对。重跑时更换目录，旧结果保留。

## 文件用途

|文件|用途与打开方式|
|---|---|
|`.md`|实验说明；GitHub直接阅读，VS Code按Ctrl+Shift+V预览|
|`.html`|本地结果页面；通过 `python -m risk_text_pilot results` 在浏览器查看|
|`.json` / `.jsonl`|配置与程序记录；编辑器可查看，结果优先看实验说明|
|`.py` / `.ps1`|代码与启动脚本|
|`.dll` / `.gguf`|本机运行库与模型权重，不是阅读文档|

核验规则见 [规范](docs/annotation-protocol.md)，模型与运行时版本见 [清单](configs/assets.json)。`data/`、`runs/`、`.runtime/`、`.venv/`保留在本机；VS Code默认隐藏运行环境目录。远程主分支为 `main`。
