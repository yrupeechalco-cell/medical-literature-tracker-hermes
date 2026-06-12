---
name: medical-literature-tracker
description: Track new medical papers, preprints, and clinical trials from official sources and prepare a cautious Chinese research digest.
version: 0.1.0
metadata:
  hermes:
    category: research
    tags: [medicine, pubmed, literature, clinical-trials]
---

# Medical Literature Tracker

Use the deterministic collector in this project. Do not browse for replacement facts when a scheduled batch is supplied.

Runtime contract: Hermes Agent with provider `deepseek` and model `deepseek-v4-pro`. Do not call Codex, OpenClaw, another agent, subagent, delegate, or an agent-backed web service.

## Run

```powershell
python hermes/collect_for_hermes.py
```

## Digest Rules

- Write in concise Chinese for a medical teacher.
- Clearly separate peer-reviewed papers, preprints, and trial registrations.
- For each included record state study design, population, intervention/comparator, outcomes, numerical results, sample size, follow-up, and limitations only when present in the supplied data.
- Never invent effect sizes, sample sizes, conclusions, or clinical recommendations.
- Put a visible warning on preprints, retractions, expressions of concern, and corrections.
- Include the exact source URL supplied in the batch.
- Rank retractions/corrections, guidelines, RCTs, meta-analyses, and major outcome trials first.
- End with: “本简报用于科研筛选，不构成临床诊疗建议。”
- After successfully producing a non-empty digest, run the supplied `mark_delivered_command` with the terminal tool.
- If `status` is `empty`, respond exactly `[SILENT]`.
