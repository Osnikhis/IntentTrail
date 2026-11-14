# IntentTrail

GitHub-first project handover prototype for public repositories. IntentTrail combines recent commit activity, open issues and pull requests, evidence-backed relationships, optional Groq-assisted intent hypotheses, and human-approved role-specific handovers.

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

## Round 3 features

1. **ML-assisted intent reconstruction (optional):** Click **Run optional Groq intent analysis** to request cautious work-theme hypotheses. The model must cite exact evidence IDs, and the app rejects hypotheses without valid evidence references. Configure the free Groq API access with environment variables:

   ```powershell
   $env:GROQ_API_KEY="your-key-here"
   $env:GROQ_MODEL="openai/gpt-oss-20b"
   ```

   The API key is never stored in the source code. If the key is missing, invalid, rate-limited, the model is unavailable, the network fails, or the response is malformed, rule-based analysis remains available. No paid fallback is used.

2. **Project relationship reconstruction:** IntentTrail links retrieved commits, issues and pull requests using shared filenames, directories, message keywords, and explicit GitHub references. These are evidence-backed associations, not proof of technical dependency or intent.

3. **Role-specific human-approved handovers:** Review findings, edit their titles, add reviewer notes, and approve or reject them. Role-specific Markdown exports (developer, maintainer, project lead) include approved findings only.

## Data handling and limitations

- Public GitHub repositories only.
- Up to 30 recent commits are summarized; issue and pull-request retrieval is bounded by the existing app implementation.
- Raw patch text is not retained in commit summaries, displayed, included in model payloads, or included in reports. Diff scanning is bounded and retains marker names only.
- Groq receives a compact, redacted metadata payload, not raw patches. Repository text is treated as untrusted evidence, not instructions.
- Rule-based findings and model hypotheses are possibilities, not verified intentions. A human must review findings before sharing.
- Relationship edges are navigational associations; shared paths or keywords do not establish a true dependency.

## Tests

```powershell
python -m unittest -v
```
