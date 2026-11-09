# IntentTrail MVP

GitHub-first handover prototype for public repositories. It combines open issue/PR clues with recent commit-change evidence to help a maintainer begin a handover review.

## Setup (Windows PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

The app retrieves up to 30 recent commits and requests each commit's available details: author, date, changed filenames, additions/deletions, patch availability, and direct GitHub link. To keep the unauthenticated API workload bounded, it retrieves no more than those 30 details in one analysis. Commit detail failures and rate limits leave the summary visible and are identified in the app and report.

Related commits are grouped using shared filenames, parent directories, or non-generic words in commit messages. Commit messages and a bounded scan of added diff lines can surface simple follow-up markers such as `WIP`, `TODO`, or `FIXME`. Raw patch content is never displayed or included in the report; at most 12,000 patch characters per commit are scanned, and only marker names are retained.

All findings are rule-based possibilities, not verified intentions. Evidence includes supporting commit messages, filenames, and source links where available. A human maintainer must verify each finding before sharing. The app does not use a trained ML model or send repository contents to an AI service.

Run the focused checks with:
```powershell
python -m unittest -v
```
