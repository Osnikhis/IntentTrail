# IntentTrail Round 3 integration

The Round 3 engine is integrated into `app.py` while preserving the original rule-based analysis and Markdown report.

## Files

- `app.py`: Streamlit UI, GitHub retrieval, existing rule-based analysis, and Round 3 integration.
- `round3_engine.py`: relationship graph, compact intent payload, Groq analysis with fallback, human-review helpers, and role-specific Markdown report.
- `test_app.py`: existing application tests plus Round 3 integration-helper tests.
- `test_round3_engine.py`: isolated Round 3 engine tests.

## Run

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

## Optional Groq configuration (free tier only)

PowerShell:

```powershell
$env:GROQ_API_KEY="your-key-here"
$env:GROQ_MODEL="openai/gpt-oss-20b"
```

Do not put the key in source code, commit it, or paste it into chat. Groq is optional. Missing keys, rate limits, invalid credentials, unavailable models, network/API failures, and malformed output fall back to the rule-based experience.

## User flow

1. Analyze a public GitHub repository as before.
2. Inspect the evidence relationship table and existing rule-based findings.
3. Optionally run Groq intent analysis. Validated hypotheses are added to the review queue.
4. Approve, reject, or reset findings; edit titles and add reviewer notes.
5. Select developer, maintainer, or project lead and download a role-specific Markdown handover. Only approved findings are included in that role-specific report.

## Role-specific report differences

- **Developer:** implementation context, changed files, approved work signals, and recent commits.
- **Maintainer:** open/closed issue and pull-request counts, open queue items, TODO/FIXME/WIP maintenance signals, and approved findings.
- **Project lead:** delivery snapshot, work themes, repository queue counts, visible contributor count, and a coordination agenda.

All role reports use the same retrieved GitHub evidence and approved findings, but organize it for different decisions. Empty queues are reported explicitly; no issues, pull requests, priorities, or dependencies are invented. Issue/PR lists reflect the records returned by the GitHub API for that analysis.

The original general-purpose Markdown report remains available. Relationship edges are associations supported by retrieved evidence, not verified technical dependencies or intent.
