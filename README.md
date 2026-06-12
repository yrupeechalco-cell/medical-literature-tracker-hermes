# 医学文献实时追踪系统 / Medical Literature Tracker

这是一个面向医学老师和研究人员的增量文献追踪实验。首个试验主题为：

> **GLP-1 受体激动剂用于肥胖人群的心血管、肾脏和安全性结局**

选择该主题是因为它更新活跃、临床意义明确，并且可以同时验证论文、预印本、临床试验、指南、勘误和撤稿追踪。

## 数据来源

- PubMed / NCBI E-utilities：主要论文来源
- Europe PMC：补充论文元数据和开放获取信息
- medRxiv：预印本与版本更新
- ClinicalTrials.gov API v2：临床试验注册与状态更新

所有原始响应、数据库和报告均保存在本项目目录下，不写入系统盘。

## 快速运行

```powershell
cd "F:\Medical_Literature_Tracker_医学文献追踪系统"
$env:PYTHONPATH = "$PWD\src"
python -m medlit_tracker run
```

常用命令：

```powershell
python -m medlit_tracker collect
python -m medlit_tracker report
python -m medlit_tracker status
python -m medlit_tracker pending --json
python -m medlit_tracker mark-delivered --batch-id <batch-id>
```

## Hermes-only 部署

生产运行只依赖 Hermes Agent 与 DeepSeek V4 Pro，不调用 Codex、OpenClaw、
其他 agent、subagent 或 delegate。安装器会把定时任务固定为 `deepseek` /
`deepseek-v4-pro`，并仅开放 Hermes 的 `terminal` 与 `file` 工具集。

将整个项目目录复制到目标机器后执行：

```powershell
python hermes/portable.py install --schedule "30 7 * * *" --deliver feishu:<chat_id>
python hermes/portable.py doctor
python hermes/portable.py test --timeout 900
```

若 `HERMES_HOME` 不在默认的 `~/.hermes`，先设置该环境变量。安装器不会复制
SQLite 数据库或原始响应到系统盘；Hermes 主目录只保存调度配置和一份小型 skill。

生成可移植包：

```powershell
python hermes/build_bundle.py
```

ZIP 默认写入 `dist/`，不包含数据库、原始响应、报告、日志、缓存或密钥。

## 目录标注

- `config/`：研究主题、查询式、关键词和来源配置
- `src/`：采集、标准化、去重、评分和报告代码
- `data/`：SQLite 数据库和运行状态
- `raw/`：按日期保存的官方 API 原始响应
- `reports/`：Markdown 和 JSON 报告
- `logs/`：运行日志
- `hermes/`：Hermes skill 和定时任务入口
- `dist/`：可交付的 Hermes-only 移植包
- `tests/`：回归测试

## 医学安全边界

本系统用于科研信息筛选，不提供临床诊断或治疗建议。模型摘要必须保留研究设计、样本量、主要结局、局限性、预印本状态以及撤稿或勘误警告。

当前 Hermes/飞书部署情况见 [`docs/部署状态.md`](docs/部署状态.md)。
