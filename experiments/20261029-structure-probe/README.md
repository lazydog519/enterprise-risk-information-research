# 结构信息利用对照

检验同一模型在原文不变时，是否受益于中间结构、是否受结构遗漏或错误干扰。共8条固定案例、8次结构提取、32次判断，40次调用全部完成，无格式失败。未训练模型。

## 数据与设置

|官方原件|证据位置（PDF物理页码）|用途|
|---|---|---|
|[中芯国际2025年年报](https://static.cninfo.com.cn/finalpage/2026-03-27/1225037057.PDF)|21、24、103|供应条件、否定范围、仲裁阶段、人员与企业归属；4条|
|[绿能慧充战略合作公告2025-038](https://static.cninfo.com.cn/finalpage/2025-08-06/1224400480.PDF)|3—4|供货安排与交付完成、框架协议效力；2条|
|[比亚迪2025年9月产销快报](https://www.hkexnews.hk/listedco/listconews/sehk/2025/1001/2025100100080_c.pdf)|1—2|审验状态、产量与销量区分；2条非风险对照|

来源URL、原件SHA256、段落锚点、待核验陈述与单字段扰动见 [固定案例](../../configs/structure-probe.json)。按完整小节或明确列出的多段证据窗口取文，保留空白及表格列间距；不做定长截断。原件和段落全文只保存在本机。

模型为Qwen3-4B Q4_K_M，权重及运行时校验值见 [资源清单](../../configs/assets.json)。本次使用llama.cpp b11960 Vulkan、RTX 3050 Ti Laptop 4GB，实际卸载20/37层，CPU线程6、上下文4096、并发1。GPU用于推理，没有进行PyTorch或CUDA训练。40次调用累计客户端耗时150.641秒，不是跨后端速度测试。

温度0、seed 42，关闭思考输出；结构提取最多768 tokens，判断最多192 tokens。所有条件均使用JSON Schema约束。[结构提取提示词](../../prompts/structure_extract_v1.txt)与[判断提示词](../../prompts/structure_judge_v1.txt)在运行前冻结。结构提取只看到原文，不看到待核验陈述、参考结构或标签。

## 四个条件

|条件|原文与陈述|辅助结构|
|---|---|---|
|direct|相同|无|
|generated|相同|同一模型从原文自然提取的结构|
|reference|相同|助手对照原文拟定的结构，作为有额外信息的诊断条件|
|corrupted|相同|只修改reference的一个指定字段，作为人为压力测试|

最终任务是判断陈述是否被原文支持：S支持，C明确相反，U依据不足。此标签不同于事件状态，更不代表客观风险概率。参考标签与参考结构均由助手核对，尚未经过两人独立复核。因此下表是参考一致数，不是经独立金标准验证的准确率。规则见 [核验规范](../../docs/annotation-protocol.md)。

## 结果

|条件|与参考标签一致|相对direct改变|其中改善／变差（按参考标签）|
|---|---:|---:|---:|
|direct|6/8|—|—|
|generated|7/8|3|2／1|
|reference|8/8|2|2／0|
|corrupted|6/8|1|0／0|

corrupted另有1条从错误的C变为错误的S，因此“判断改变”不总是改善或变差。reference与corrupted直接配对时，2/8条标签改变。

三处可复查现象：

- **供应风险**：原文给出未来条件及可能影响。direct判C，generated与reference判U。模型把“未证实已经发生”与“明确未发生”的区别处理不稳定。
- **灾害影响**：自然结构漏掉报告期内未受到营运影响的否定信息；direct为C，加入该结构后变为U。原文仍在输入中，信息遗漏仍可能干扰判断。
- **仲裁进展**：自然结构保留“提出撤裁申请”，使C变为U；人为把状态改成“法院已撤销”后，模型判S，理由中把辅助结构内容当成原文事实。

另外6/8组reference—corrupted配对未改变标签。模型并非总接受错误辅助信息。本轮观察到的是固定小模型对文本化中间结构的敏感性，尚不能推广为图融合或联合训练的收益，也不能证明这些现象在更强模型上同样成立。自然提取最多保留3条事件，其压缩遗漏与模型理解错误仍需分别核验。

逐条标签及运行标识见 [results.json](results.json)；原文、自然结构、判断理由和全部原始响应见本机 `runs/20261029_structure_probe_v2/review.html`。

## 复现

安装步骤见 [仓库首页](../../README.md)。服务需加载清单中的同一权重，并支持JSON Schema。CPU服务可检验流程；复现本轮GPU设置时，在 `.runtime` 目录启动Vulkan版本：

```text
llama-b11960-vulkan/llama-server.exe --model D:/AI-Models/risk-text-pilot/Qwen3-4B-Q4_K_M.gguf --host 127.0.0.1 --port 18081 --alias qwen3-4b-pilot-gpu --ctx-size 4096 --threads 6 --n-gpu-layers 20 --device Vulkan0 --parallel 1 --reasoning-budget 0 --api-key-file inference.key
```

Vulkan运行库通过Python入口准备：

```powershell
.\.venv\Scripts\python.exe -m risk_text_pilot bootstrap --runtime-only --backend vulkan --model-dir D:/AI-Models/risk-text-pilot
```

在仓库根目录运行，使用新目录保留旧结果：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m risk_text_pilot probe prepare --run-dir runs/structure_probe_repeat
.\.venv\Scripts\python.exe -X utf8 -u -m risk_text_pilot probe run --run-dir runs/structure_probe_repeat --base-url http://127.0.0.1:18081/v1 --hardware "Vulkan0 RTX3050Ti 20GPUlayers ctx4096 threads6"
.\.venv\Scripts\python.exe -X utf8 -m risk_text_pilot probe report --run-dir runs/structure_probe_repeat
.\.venv\Scripts\python.exe -m risk_text_pilot results --run-dir runs/structure_probe_repeat
```

本轮初次组装输入时，去空白处理会合并表格数字列，运行中止；修复后对全部8条重新运行，案例、提示词、模型与参考标签均未调整。中止日志保留在本机 `runs/20261029_structure_probe_v1/`，不混入上述统计。原有宁德时代实验保留。
