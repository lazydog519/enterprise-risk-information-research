# 企业风险文本预实验

独立工程，用于原文核查和本地模型试跑，不修改其他 VS Code 项目。当前没有确定最终主体数据集或创新点。

## 固定的研究口径

研究企业经营风险的文本表达：对象、事件、影响联系、时间和发生状态是否被正确理解，以及怎样估计记录的可靠性。
DocFEE仅是辅助机制资源；论文、项目申请书、AI生成稿不进入企业原文语料。基金报告提供背景，不决定全部实验。
先核对文本和人工可审定规则，再判断结构依赖的价值。人工核验为辅助，不展开复核分配、耗时预测或批次质量推断。
本阶段不实现RAG/Agent、独立风险传播模型、韧性评价、强化学习、迁移学习或模型训练。

本地模型是可替换的 **Qwen3-4B Q4_K_M推理基线**，不是论文最终模型。推理结果不是人工真值。
`status`是原文表达的发生状态，不是模型信心。引用匹配仅确认文字能定位，不能证明语义正确。

旧企业报告尚未定位。本轮新增宁德时代2025年年报，用管理层讨论与分析部分做连通性试读；来源和哈希见`public_pilot_sources.json`，不将其当作最终主体数据集。

## 本机使用

在新 VS Code 窗口打开本目录。已有独立`.venv`时直接使用；新机器先执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 pilot.py doctor
.\.venv\Scripts\python.exe -X utf8 -m unittest -v
```

只从官方地址下载锁定版本，校验SHA256。权重约2.50 GB，CPU运行，不改变显卡驱动；首次联网只下载公开软件和权重。

```powershell
.\.venv\Scripts\python.exe -X utf8 -u bootstrap_local.py --model-dir D:\AI-Models\risk-text-pilot
.\serve.ps1 -Background
.\.venv\Scripts\python.exe -X utf8 pilot.py doctor
```

服务仅监听`127.0.0.1:18080`，限制跨源访问并使用本项目生成的本地密钥；密钥不显示、不提交Git。用`.\serve.ps1 -Stop`停止本项目启动的进程。
模型路径保存在被Git忽略的`settings.local.json`。不上传文档到商业API，不安装全局软件。

## 登记、试读与验证

原文件只读引用，不自动扫描论文作为样本。登记时由人工确认文档性质；原文哈希变化会阻止继续提取。

```powershell
.\.venv\Scripts\python.exe -X utf8 pilot.py register --path "实际企业报告.pdf" --company "企业名称" --kind annual_report --url "原始发布链接" --date "2026-03-10"
.\.venv\Scripts\python.exe -X utf8 pilot.py ingest --page-start 14 --page-end 41
.\.venv\Scripts\python.exe -X utf8 pilot.py sample --limit 12
.\.venv\Scripts\python.exe -X utf8 -u pilot.py infer --limit 3 --run-dir runs\第一次试跑
```

页码是PDF物理页码，从1开始；示例范围必须按所用报告修改。抽样保留关键词候选和随机对照片段，属于开发性试读，不用于估计总体错误率。
保留页码、字符位置、原始回答及模型/提示词版本。片段可能截断跨页语境，需要回看原报告。

查看`runs/第一次试跑/review.html`，同时回查原PDF；不要把格式成功率当准确率。
`annotation_a.jsonl`和`annotation_b.jsonl`供两人独立标注，未标注为null，不会自动写成正确标签。
现阶段只计算格式失败和引用定位；语义错误率与校准误差保持null。

若需查找旧资料，可执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 pilot.py inventory "实际研究目录" --out data\inventory.jsonl
```

所有生成文件默认拒绝覆盖。重新试验请更换输出路径/运行目录，不删除旧结果。

## 版本与资料管理

只提交代码、提示词和公开来源说明。`data/`、`runs/`、`.runtime/`、`.venv/`、权重和本地配置不进入Git。
当前只保存本地Git版本；未指定远程仓库前不创建或推送GitHub仓库。

模型来源：https://huggingface.co/Qwen/Qwen3-4B-GGUF

运行环境来源：https://github.com/ggml-org/llama.cpp/releases/tag/b11960
