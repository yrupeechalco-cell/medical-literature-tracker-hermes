# Project Instructions

- Keep all project code, data, raw API responses, reports, and logs under this directory on `F:`.
- Prefer official APIs over HTML scraping.
- Never present preprints as peer-reviewed evidence.
- Preserve source provenance and identifiers for every record.
- Do not delete historical records during routine collection.
- Production runtime is Hermes Agent only. Do not call Codex, OpenClaw, another
  agent, subagent, delegate, or an agent-backed browsing service.
- Scheduled runs must pin provider `deepseek`, model `deepseek-v4-pro`, and only
  the Hermes `terminal` and `file` toolsets.
- Run `python -m unittest discover -s tests -v` after code changes.
