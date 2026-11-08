# IntentTrail MVP

GitHub-first handover prototype for public repositories.

## Setup (Windows PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

This early version uses transparent rules to surface open issues and pull requests as possible unfinished-work signals. It does not infer private intentions.
