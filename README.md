# Enterprise Risk Information Research

企业风险文本实验：数据来源、处理流程、对照设置与结果。

## 实验索引

|实验|设计与结果|结果文件|
|---|---|---|
|20261029：段落完整性 × 任务规则|[实验说明](experiments/20261029-paragraph-control/README.md)|[results.json](experiments/20261029-paragraph-control/results.json)|

数据来源见 [public_pilot_sources.json](public_pilot_sources.json)，标注规则见 [annotation-protocol.md](docs/annotation-protocol.md)，模型与运行时版本见 [assets.json](assets.json)。

## 安装

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 -m unittest -q
.\.venv\Scripts\python.exe -X utf8 bootstrap_local.py --model-dir D:\AI-Models\risk-text-pilot
.\serve.ps1 -Background
```

服务地址为 `http://127.0.0.1:18080/v1`，本地配置存于 `settings.local.json`。停止服务：`.\serve.ps1 -Stop`。

## 准备数据

从数据来源清单下载对应PDF，保存为 `data/raw/CATL_2025_annual_report.pdf`，然后登记并提取文本：

```powershell
.\.venv\Scripts\python.exe -X utf8 pilot.py register --path data/raw/CATL_2025_annual_report.pdf --company "宁德时代新能源科技股份有限公司" --kind annual_report --url "https://static.cninfo.com.cn/finalpage/2026-03-10/1225002214.PDF" --date 2026-03-10
.\.venv\Scripts\python.exe -X utf8 pilot.py ingest --page-start 14 --page-end 41
```

清单包含来源链接、SHA256和PDF物理页码；原文件保留在本机。

## 复现对照

```powershell
.\.venv\Scripts\python.exe -X utf8 controlled_check.py prepare --out runs/20261029_paragraph_control_v2
.\.venv\Scripts\python.exe -X utf8 -u controlled_check.py run --out runs/20261029_paragraph_control_v2
```

输出目录包含输入、提示词及模型清单、原始回答、校验结果和 `comparison.html`。使用浏览器阅读HTML；JSON/JSONL用于程序核对。重跑时更换目录，旧结果保留。

## 目录约定

- 根目录：推理、数据处理与测试代码。
- `experiments/`：按日期归档实验设计和结果。
- `docs/`：操作及标注规范。
- `data/`、`runs/`、`.runtime/`、`.venv/`：本机资料、输出和运行环境，不进入Git。

Python文件使用 `snake_case`；实验说明目录使用 `YYYYMMDD-short-name`；本机运行目录使用 `YYYYMMDD_experiment_name_vN`。远程主分支为 `main`。
