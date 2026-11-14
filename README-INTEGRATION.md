# IntentTrail Round 3 integration

The Round 3 engine is integrated into `app.py` while preserving the original rule-based analysis and Markdown report. The primary product output is now a ranked, evidence-linked next work path.

## Files

- `app.py`: Streamlit UI, GitHub retrieval, existing rule-based analysis, and Round 3 integration.
- `round3_engine.py`: relationship graph, compact intent payload, Groq analysis with fallback, ranked next-work-path engine, human-review helpers, and role-specific Markdown report.
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
2. Inspect the evidence relationship graph.
3. Review the editable project-goal draft, correct it to match the original problem statement, and run **Reconstruct missing development intent**.
4. Inspect the likely direction, current state, possible missing pieces, and next paths. Each displayed reconstruction item must cite fetched repository evidence.
5. Review the **Predicted next work path**: ranked recommendations show why they were suggested, what to do next, and links to supporting GitHub records. Priority scores are heuristic rankings, not probabilities.
6. Approve, reject, or reset findings; edit titles and add reviewer notes. Reconstructed directions, gaps, and paths enter the review queue rather than being shared automatically.
7. Select developer, maintainer, or project lead and download a role-specific Markdown handover. Only approved findings are included in that role-specific report.

## Role-specific report differences

- **Developer:** implementation context, changed files, approved work signals, and recent commits.
- **Maintainer:** open/closed issue and pull-request counts, open queue items, TODO/FIXME/WIP maintenance signals, and approved findings.
- **Project lead:** delivery snapshot, work themes, repository queue counts, visible contributor count, and a coordination agenda.

All role reports use the same retrieved GitHub evidence and approved findings, but organize it for different decisions. Empty queues are reported explicitly; no issues, pull requests, priorities, or dependencies are invented. Issue/PR lists reflect the records returned by the GitHub API for that analysis.

The original general-purpose Markdown report remains available. Relationship edges are associations supported by retrieved evidence, not verified technical dependencies or intent.

## Next-work ranking rules

- Open issues and open pull requests are ranked as stronger direct workflow signals.
- TODO/FIXME/WIP markers create cautious follow-up candidates, never automatic assertions that work is unfinished.
- Related commit clusters create low-priority review/test suggestions, not assertions that work remains.
- Goal-aware Groq reconstruction compares the editable project goal with repository evidence. Each direction, gap, path, or hypothesis is included only when at least one evidence reference resolves to a fetched commit, issue, or pull request.
- Evidence URLs are displayed with each recommendation. Missing or unsupported evidence is not invented.
- The numeric priority is a transparent heuristic score used for ordering; it is not a probability, model accuracy measure, or prediction of a particular developer's private intent. The ranked list can be downloaded as Markdown.
