# IntentTrail

GitHub-first next-work-path prediction and project handover prototype for public repositories. IntentTrail extracts repository activity, reconstructs evidence-backed relationships, and ranks plausible next development actions with source links and human verification. Optional Groq hypotheses enrich the ranking; role-specific handovers remain available.

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

## Core product: reconstruct missing intent, then predict the next path

After repository analysis, IntentTrail offers an editable project-goal field. It drafts a starting point from the repository README introduction or description; the developer should correct it to match the original problem statement. When optional Groq analysis is available, IntentTrail compares that intended destination with repository evidence and returns a cautious reconstruction: likely development direction, current visible state, possible missing pieces, and evidence-backed next paths.

The **Predicted next work path** then combines those goal-aware paths with rule-based repository signals. It turns extracted repository evidence into concrete follow-up actions:

- Open issues and pull requests become high-priority triage/review recommendations.
- TODO/FIXME/WIP markers become possible follow-up tasks that must be checked against the source commit.
- Related commit clusters can suggest an exploratory review/test path for repeatedly touched files or directories; this is not proof of unfinished work.
- Optional Groq hypotheses add lower-priority work themes only when their evidence references match retrieved repository records.
- Each recommendation shows its evidence basis, suggested next steps, and links to the source records.
- Priority scores are transparent heuristic rankings, **not probabilities or claims about developer intent**. Developers must verify recommendations before acting. The ranked path can be downloaded as Markdown.

## Round 3 features

1. **Goal-aware intent reconstruction (optional):** Correct the project-goal draft, then click **Reconstruct missing development intent**. Groq compares the goal with repository evidence and proposes a likely direction, possible gaps, and next paths. The app rejects reconstruction items without valid evidence references. These are hypotheses, not facts, and must be human-reviewed. Configure free Groq API access with environment variables:

   ```powershell
   $env:GROQ_API_KEY="your-key-here"
   $env:GROQ_MODEL="openai/gpt-oss-20b"
   ```

   The API key is never stored in the source code. If the key is missing, invalid, rate-limited, the model is unavailable, the network fails, or the response is malformed, rule-based analysis remains available. No paid fallback is used.

2. **Project relationship reconstruction:** IntentTrail links retrieved commits, issues and pull requests using shared filenames, directories, message keywords, and explicit GitHub references. These are evidence-backed associations, not proof of technical dependency or intent.

3. **Role-specific human-approved handovers:** Review findings, edit their titles, add reviewer notes, and approve or reject them. Role-specific Markdown exports (developer, maintainer, project lead) include approved findings only.

4. **Next work path prediction:** Combine open work queues, follow-up markers, relationship evidence, and validated Groq hypotheses into a ranked list of actionable next steps. The rules-first ranking works without Groq.

## Data handling and limitations

- Public GitHub repositories only.
- Up to 30 recent commits are summarized; issue and pull-request retrieval is bounded by the existing app implementation.
- Raw patch text is not retained in commit summaries, displayed, included in model payloads, or included in reports. Diff scanning is bounded and retains marker names only.
- Groq receives a compact, redacted metadata payload, not raw patches. Repository text is treated as untrusted evidence, not instructions.
- Without Groq, the existing rule-based relationship mapping and next-work recommendations remain available, but goal-aware AI reconstruction is not performed.
- Rule-based findings, next-work recommendations, and model reconstructions are possibilities, not verified intentions. Priority scores are not calibrated probabilities. A developer must verify source evidence before acting.
- Relationship edges are navigational associations; shared paths or keywords do not establish a true dependency.

## Tests

```powershell
python -m unittest -v
```
