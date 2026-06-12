$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONPATH = Join-Path $Root "src"
python -m medlit_tracker --topic (Join-Path $Root "config\topic_glp1_obesity.json") --db (Join-Path $Root "data\medlit_tracker.sqlite3") run

