import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="IntentTrail", page_icon="🧭", layout="wide")
API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

def parse_repo_url(url):
    url = url.strip()
    if not url:
        raise ValueError("Enter a GitHub repository URL.")
    if "://" not in url and "/" in url:
        url = "https://github.com/" + url
    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        raise ValueError("Use a github.com repository URL.")
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError("URL should look like https://github.com/owner/repo")
    return parts[0], re.sub(r"\.git$", "", parts[1])

def api_get(path, params=None):
    r = requests.get(API + path, headers=HEADERS, params=params, timeout=25)
    if r.status_code == 404:
        raise RuntimeError("Repository not found or private. This MVP supports public repos.")
    if r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0":
        raise RuntimeError("GitHub API rate limit reached. Wait and try again.")
    r.raise_for_status()
    return r.json()

def fetch(owner, repo):
    root = f"/repos/{owner}/{repo}"
    meta = api_get(root)
    commits = api_get(root + "/commits", {"per_page": 100})
    raw_issues = api_get(root + "/issues", {"state": "all", "per_page": 100})
    issues = [x for x in raw_issues if "pull_request" not in x]
    pulls = api_get(root + "/pulls", {"state": "all", "per_page": 100})
    return meta, commits, issues, pulls

def date_only(s):
    if not s:
        return "Unknown"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return s[:10]

def analyze(issues, pulls):
    findings = []
    for x in issues:
        if x.get("state") == "open":
            findings.append({
                "kind": "Open issue", "title": x.get("title", "Untitled"),
                "body": x.get("body") or "No description provided.",
                "date": date_only(x.get("updated_at")), "url": x.get("html_url", ""),
                "why": "An open issue may represent unresolved work. Verify its current status.",
            })
    for x in pulls:
        if x.get("state") == "open":
            findings.append({
                "kind": "Open pull request", "title": x.get("title", "Untitled"),
                "body": x.get("body") or "No description provided.",
                "date": date_only(x.get("updated_at")), "url": x.get("html_url", ""),
                "why": "An open PR may contain work awaiting review, revision, or merging.",
            })
    if not findings:
        findings.append({
            "kind": "No open-work signal found",
            "title": "No open issues or pull requests detected in retrieved records",
            "body": "This does not prove the project is complete. Work may exist in branches, commits, or other sources.",
            "date": "—", "url": "",
            "why": "This MVP checks a limited set of GitHub activity.",
        })
    return findings

def report(meta, findings, commits):
    lines = [
        f"# IntentTrail Handover Report: {meta.get('full_name', '')}", "",
        "> Early MVP report. Signals are not verified intentions; review before relying on them.", "",
        "## Repository overview",
        f"- Repository: {meta.get('html_url', '')}",
        f"- Description: {meta.get('description') or 'Not provided'}",
        f"- Default branch: {meta.get('default_branch', 'Unknown')}",
        f"- Commits retrieved: {len(commits)}", "",
        "## Potential unfinished work", ""
    ]
    for i, f in enumerate(findings, 1):
        lines += [f"### {i}. {f['title']}", f"- Signal: {f['kind']}",
                  f"- Updated: {f['date']}", f"- Interpretation: {f['why']}",
                  f"- Description: {f['body']}"]
        if f["url"]:
            lines.append(f"- Evidence: {f['url']}")
        lines.append("")
    lines += ["## Limitations",
               "- This version uses transparent rules, not a trained ML model.",
               "- Open issues and PRs are clues, not proof of unfinished intent.",
               "- Only up to 100 records per activity endpoint are retrieved.",
               "- A human maintainer should verify findings before sharing.",
               "", f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"]
    return "\n".join(lines)

st.title("🧭 IntentTrail")
st.subheader("Don't just inherit the code. Understand the journey behind it.")
st.write("GitHub-first project handover prototype: surface possible unfinished work with source evidence.")
st.info("Public GitHub repositories only. This MVP identifies signals; it does not claim to know a developer's intentions.")

with st.form("repo_form"):
    url = st.text_input("Public GitHub repository URL", placeholder="https://github.com/owner/repo")
    go = st.form_submit_button("Analyze repository", type="primary")

if go:
    try:
        owner, repo = parse_repo_url(url)
        with st.spinner("Fetching GitHub activity..."):
            meta, commits, issues, pulls = fetch(owner, repo)
            findings = analyze(issues, pulls)
        st.session_state["result"] = (meta, commits, issues, pulls, findings)
        st.success(f"Analysis complete: {meta.get('full_name')}")
    except requests.exceptions.Timeout:
        st.error("GitHub request timed out. Try again.")
    except requests.exceptions.ConnectionError:
        st.error("Could not connect. Check your internet connection.")
    except requests.exceptions.HTTPError as e:
        st.error(f"GitHub API error: {e}")
    except (ValueError, RuntimeError) as e:
        st.error(str(e))
    except Exception as e:
        st.error(f"Unexpected error: {e}")

if "result" in st.session_state:
    meta, commits, issues, pulls, findings = st.session_state["result"]
    st.divider()
    st.header(meta.get("full_name", "Repository"))
    st.markdown(f"[Open repository on GitHub]({meta.get('html_url', '')})")
    a, b, c, d = st.columns(4)
    a.metric("Commits", len(commits))
    b.metric("Issues", len(issues))
    c.metric("Pull requests", len(pulls))
    d.metric("Potential signals", len(findings))
    st.write(meta.get("description") or "No description provided.")
    st.caption(f"Default branch: {meta.get('default_branch')} | Stars: {meta.get('stargazers_count')} | Forks: {meta.get('forks_count')}")

    st.subheader("Potential unfinished work")
    st.warning("These are signals for human investigation, not verified intentions. Issues and PRs can be stale or intentionally open.")
    for i, f in enumerate(findings, 1):
        with st.expander(f"{i}. [{f['kind']}] {f['title']}"):
            st.write(f["body"])
            st.write("**Updated:**", f["date"])
            st.write("**Why surfaced:**", f["why"])
            if f["url"]:
                st.markdown(f"[View source evidence]({f['url']})")

    rows = []
    for x in commits[:30]:
        cd = x.get("commit", {})
        author = cd.get("author") or {}
        rows.append({"Date": date_only(author.get("date")),
                     "Message": (cd.get("message") or "").splitlines()[0],
                     "Author": author.get("name", "Unknown"),
                     "SHA": x.get("sha", "")[:7],
                     "URL": x.get("html_url", "")})
    st.subheader("Recent commits")
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.write("No commits returned.")

    st.download_button("Download handover report (Markdown)",
                       report(meta, findings, commits),
                       file_name=f"intenttrail_{meta.get('name', 'repo')}_handover.md",
                       mime="text/markdown", type="primary")
    st.caption("Human review is required before sharing this handover.")

st.divider()
st.caption("IntentTrail MVP | GitHub-first | Evidence-aware | Human-reviewed")