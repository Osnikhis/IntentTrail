import base64
import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st

import round3_engine

API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
COMMIT_LIMIT = 30
MAX_FILES_PER_COMMIT = 100
MAX_DIFF_CHARS_PER_COMMIT = 12_000
HUMAN_REVIEW = "This is a rule-based possibility, not a verified intention. A human maintainer must verify it before sharing."
UNFINISHED_MESSAGE_MARKERS = (
    "wip",
    "todo",
    "fixme",
    "follow up",
    "unfinished",
    "draft",
    "temporary",
    "later",
    "not implemented",
    "incomplete",
    "placeholder",
    "revisit",
)
DIFF_MARKERS = {
    "TODO": re.compile(r"\bTODO\b", re.IGNORECASE),
    "FIXME": re.compile(r"\bFIXME\b", re.IGNORECASE),
    "WIP": re.compile(r"\bWIP\b", re.IGNORECASE),
}
MESSAGE_STOPWORDS = {
    "about", "after", "again", "also", "and", "are", "because", "before", "being",
    "between", "but", "change", "changes", "chore", "code", "commit", "docs", "feat",
    "fix", "fixed", "for", "from", "into", "make", "more", "new", "not", "our",
    "refactor", "remove", "removed", "test", "tests", "the", "this", "through", "to",
    "update", "updated", "using", "with",
    "initial", "intenttrial", "intenttrail", "added",
}

# Generated / environment paths are not project work. They are excluded from
# theme grouping and marker scanning, but their commits stay listed for audit.
GENERATED_DIRS = {".venv", "venv", "site-packages", "__pycache__", "node_modules"}
GENERATED_SUFFIXES = (".pyc", ".pyo", ".pyd")
ENVIRONMENT_HEAVY_RATIO = 0.5


def is_generated_path(path):
    p = (path or "").replace("\\", "/").lower()
    return any(part in GENERATED_DIRS for part in p.split("/")[:-1]) or p.endswith(GENERATED_SUFFIXES)


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
    response = requests.get(API + path, headers=HEADERS, params=params, timeout=25)
    if response.status_code == 404:
        raise RuntimeError("Repository not found or private. This MVP supports public repos.")
    is_rate_limited = response.status_code == 429 or (
        response.status_code == 403
        and (
            response.headers.get("X-RateLimit-Remaining") == "0"
            or "rate limit" in response.text.lower()
        )
    )
    if is_rate_limited:
        raise RuntimeError("GitHub API rate limit reached. Wait and try again.")
    response.raise_for_status()
    return response.json()


def _commit_message(commit):
    return (commit.get("commit") or {}).get("message") or "Commit message unavailable"


def _commit_date(commit):
    commit_data = commit.get("commit") or {}
    author_data = commit_data.get("author") or {}
    committer_data = commit_data.get("committer") or {}
    return author_data.get("date") or committer_data.get("date")


def _commit_author(commit):
    account = commit.get("author") or {}
    author_data = (commit.get("commit") or {}).get("author") or {}
    return account.get("login") or author_data.get("name") or "Unknown"


def _scan_patch_files(files):
    markers = set()
    patch_chars = 0
    patch_count = 0
    patch_truncated = False
    remaining = MAX_DIFF_CHARS_PER_COMMIT

    for file_index, changed_file in enumerate(files):
        patch = changed_file.get("patch")
        if not patch:
            continue
        patch_count += 1
        if remaining <= 0:
            patch_truncated = True
            continue
        scanned = patch[:remaining]
        patch_chars += len(scanned)
        remaining -= len(scanned)
        if len(scanned) < len(patch):
            patch_truncated = True
        for line in scanned.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                for marker, pattern in DIFF_MARKERS.items():
                    if pattern.search(line):
                        markers.add(marker)
        if remaining == 0:
            patch_truncated = patch_truncated or any(
                bool(file.get("patch"))
                for file in files[file_index + 1:]
            )
            break
    return sorted(markers), patch_count, patch_chars, patch_truncated


def _detail_error_message(error):
    if isinstance(error, RuntimeError):
        return str(error)
    if isinstance(error, requests.exceptions.Timeout):
        return "Timed out while fetching commit details."
    if isinstance(error, requests.exceptions.HTTPError):
        status = error.response.status_code if error.response is not None else "unknown"
        return f"GitHub API returned HTTP {status} while fetching commit details."
    if isinstance(error, requests.exceptions.RequestException):
        return "GitHub API request failed while fetching commit details."
    return "Commit details could not be fetched."


def _enrich_commit(owner, repo, commit):
    sha = commit.get("sha", "")
    detail = api_get(f"/repos/{owner}/{repo}/commits/{sha}")
    detail_commit = detail.get("commit") or {}
    detail_files = detail.get("files") or []
    all_filenames = [file.get("filename") for file in detail_files if file.get("filename")]
    filenames = [name for name in all_filenames if not is_generated_path(name)]
    generated_count = len(all_filenames) - len(filenames)
    analysis_files = [f for f in detail_files if not is_generated_path(f.get("filename"))]
    markers, patch_count, patch_chars, patch_truncated = _scan_patch_files(analysis_files)
    stats = detail.get("stats") or {}
    return {
        "sha": sha,
        "message": detail_commit.get("message") or _commit_message(commit),
        "author": _commit_author(detail),
        "date": _commit_date(detail) or _commit_date(commit),
        "url": detail.get("html_url") or commit.get("html_url") or "",
        "filenames": filenames[:MAX_FILES_PER_COMMIT],
        "file_count": len(all_filenames),
        "generated_file_count": generated_count,
        "environment_heavy": bool(all_filenames)
        and generated_count / len(all_filenames) >= ENVIRONMENT_HEAVY_RATIO,
        "filenames_truncated": len(filenames) > MAX_FILES_PER_COMMIT,
        "additions": stats.get("additions"),
        "deletions": stats.get("deletions"),
        "patch_count": patch_count,
        "patch_chars_scanned": patch_chars,
        "patch_truncated": patch_truncated,
        "patch_markers": markers,
        "detail_available": True,
        "detail_error": "",
    }


def _summary_commit(commit, owner, repo, error):
    sha = commit.get("sha", "")
    url = commit.get("html_url") or (
        f"https://github.com/{owner}/{repo}/commit/{sha}" if sha else ""
    )
    return {
        "sha": sha,
        "message": _commit_message(commit),
        "author": _commit_author(commit),
        "date": _commit_date(commit),
        "url": url,
        "filenames": [],
        "file_count": None,
        "generated_file_count": 0,
        "environment_heavy": False,
        "filenames_truncated": False,
        "additions": None,
        "deletions": None,
        "patch_count": 0,
        "patch_chars_scanned": 0,
        "patch_truncated": False,
        "patch_markers": [],
        "detail_available": False,
        "detail_error": _detail_error_message(error),
    }


def _fetch_readme(owner, repo):
    """Fetch a bounded README excerpt; missing README must not block analysis."""
    try:
        response = requests.get(
            f"{API}/repos/{owner}/{repo}/readme", headers=HEADERS, timeout=15
        )
        if response.status_code == 404:
            return ""
        response.raise_for_status()
        payload = response.json()
        encoded = payload.get("content", "")
        if not encoded:
            return ""
        raw = base64.b64decode(encoded).decode("utf-8", errors="replace")
        return raw[:8000]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return ""


def _draft_project_goal(meta):
    """Create an editable goal draft from README introduction or repo description."""
    readme = meta.get("readme_text", "") or ""
    readme = re.sub(r"```.*?```", " ", readme, flags=re.DOTALL)
    readme = re.sub(r"!\[[^]]*\]\([^)]*\)", " ", readme)
    readme = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", readme)
    candidates = []
    for line in readme.splitlines():
        line = line.strip().lstrip("#*-> ").strip()
        if not line or line.startswith(("[!", "<", "|")):
            continue
        if len(line) < 35:
            continue
        candidates.append(line)
        if len(candidates) >= 3:
            break
    if candidates:
        return " ".join(candidates)[:1200]
    return str(meta.get("description") or "")[:1200]


def fetch(owner, repo):
    root = f"/repos/{owner}/{repo}"
    meta = api_get(root)
    meta["readme_text"] = _fetch_readme(owner, repo)
    summary_commits = api_get(root + "/commits", {"per_page": COMMIT_LIMIT})
    raw_issues = api_get(root + "/issues", {"state": "all", "per_page": 100})
    issues = [item for item in raw_issues if "pull_request" not in item]
    pulls = api_get(root + "/pulls", {"state": "all", "per_page": 100})

    commits = []
    stop_detail_requests = False
    stop_detail_message = ""
    for commit in summary_commits:
        if stop_detail_requests:
            commits.append(_summary_commit(
                commit, owner, repo,
                RuntimeError(stop_detail_message),
            ))
            continue
        try:
            commits.append(_enrich_commit(owner, repo, commit))
        except (requests.exceptions.RequestException, RuntimeError) as error:
            commits.append(_summary_commit(commit, owner, repo, error))
            stop_detail_requests = (
                isinstance(error, RuntimeError) and "rate limit" in str(error).lower()
            ) or isinstance(error, (requests.exceptions.Timeout, requests.exceptions.ConnectionError))
            if stop_detail_requests:
                stop_detail_message = _detail_error_message(error)

    return meta, commits, issues, pulls


def date_only(value):
    if not value:
        return "Unknown"
    if not isinstance(value, str):
        return "Unknown"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return value[:10]


def _message_keywords(message):
    words = re.findall(r"[a-z0-9][a-z0-9_-]{3,}", (message or "").lower())
    return {word for word in words if word not in MESSAGE_STOPWORDS}


def group_related_commits(commits):
    """Return connected groups, with the exact transparent rules that joined them.

    Environment-heavy commits (mostly .venv / site-packages / cache files) are
    skipped: dependency files are not evidence of project work.
    """
    links = []
    for left_index in range(len(commits)):
        left = commits[left_index]
        if left.get("environment_heavy"):
            continue
        left_files = set(left.get("filenames") or [])
        left_dirs = {
            PurePosixPath(path).parent.as_posix()
            for path in left_files
            if PurePosixPath(path).parent.as_posix() != "."
        }
        left_keywords = _message_keywords(left.get("message", ""))
        for right_index in range(left_index + 1, len(commits)):
            right = commits[right_index]
            if right.get("environment_heavy"):
                continue
            right_files = set(right.get("filenames") or [])
            right_dirs = {
                PurePosixPath(path).parent.as_posix()
                for path in right_files
                if PurePosixPath(path).parent.as_posix() != "."
            }
            shared_files = left_files & right_files
            shared_dirs = left_dirs & right_dirs
            shared_keywords = left_keywords & _message_keywords(right.get("message", ""))
            if shared_files or shared_dirs or shared_keywords:
                links.append({
                    "left": left_index,
                    "right": right_index,
                    "shared_files": shared_files,
                    "shared_dirs": shared_dirs,
                    "shared_keywords": shared_keywords,
                })

    adjacency = {index: set() for index in range(len(commits))}
    for link in links:
        adjacency[link["left"]].add(link["right"])
        adjacency[link["right"]].add(link["left"])

    groups = []
    visited = set()
    for start in range(len(commits)):
        if start in visited or not adjacency[start]:
            continue
        stack = [start]
        members = set()
        while stack:
            current = stack.pop()
            if current in members:
                continue
            members.add(current)
            stack.extend(adjacency[current] - members)
        visited.update(members)
        group_links = [
            link for link in links
            if link["left"] in members and link["right"] in members
        ]
        shared_files = set().union(*(link["shared_files"] for link in group_links))
        shared_dirs = set().union(*(link["shared_dirs"] for link in group_links))
        shared_keywords = set().union(*(link["shared_keywords"] for link in group_links))
        reasons = []
        if shared_files:
            reasons.append("shared filename(s): " + ", ".join(sorted(shared_files)[:3]))
        if shared_dirs:
            reasons.append("shared directory/directories: " + ", ".join(sorted(shared_dirs)[:3]))
        if shared_keywords:
            reasons.append("shared message keyword(s): " + ", ".join(sorted(shared_keywords)[:3]))
        groups.append({
            "commits": [commits[index] for index in sorted(members)],
            "shared_files": sorted(shared_files),
            "shared_dirs": sorted(shared_dirs),
            "shared_keywords": sorted(shared_keywords),
            "reasons": reasons,
        })
    return groups


def _finding(kind, title, why, commits=None, filenames=None, date="—", url="", body=""):
    commits = commits or []
    return {
        "kind": kind,
        "title": title,
        "body": body,
        "date": date,
        "url": url,
        "why": why,
        "commit_messages": [commit.get("message", "Commit message unavailable") for commit in commits],
        "filenames": sorted(set(filenames or [])),
        "commit_urls": [commit.get("url") for commit in commits if commit.get("url")],
        "disclaimer": HUMAN_REVIEW,
    }


def analyze(issues, pulls, commits=None):
    findings = []
    for item in issues:
        if item.get("state") == "open":
            findings.append(_finding(
                "Open issue",
                item.get("title", "Untitled"),
                "An open issue may represent unresolved work. Verify its current status.",
                date=date_only(item.get("updated_at")),
                url=item.get("html_url", ""),
                body=item.get("body") or "No description provided.",
            ))
    for item in pulls:
        if item.get("state") == "open":
            findings.append(_finding(
                "Open pull request",
                item.get("title", "Untitled"),
                "An open PR may contain work awaiting review, revision, or merging.",
                date=date_only(item.get("updated_at")),
                url=item.get("html_url", ""),
                body=item.get("body") or "No description provided.",
            ))

    commits = commits or []
    for group in group_related_commits(commits):
        group_commits = group["commits"]
        evidence = group["shared_files"] or group["shared_dirs"] or group["shared_keywords"]
        theme = ", ".join(evidence[:2]) if evidence else "related commit activity"
        findings.append(_finding(
            "Possible work theme",
            f"Related commits around {theme}",
            "Grouped because the commits have " + "; ".join(group["reasons"]) + ".",
            commits=group_commits,
            filenames=[
                filename
                for commit in group_commits
                for filename in commit.get("filenames", [])
            ],
            date="; ".join(sorted({date_only(commit.get("date")) for commit in group_commits})),
            body="The grouping is a navigation aid, not proof that these commits share an intended outcome.",
        ))

    for commit in commits:
        message = commit.get("message", "")
        matched_message_markers = [
            marker for marker in UNFINISHED_MESSAGE_MARKERS
            if re.search(rf"\b{re.escape(marker)}\b", message, re.IGNORECASE)
        ]
        diff_markers = commit.get("patch_markers") or []
        markers = matched_message_markers + [
            marker for marker in diff_markers if marker.lower() not in matched_message_markers
        ]
        if markers:
            source = []
            if matched_message_markers:
                source.append("commit message")
            if diff_markers:
                source.append("added diff lines")
            findings.append(_finding(
                "Possible unfinished work",
                f"Possible follow-up marker in: {message.splitlines()[0][:120]}",
                "Matched marker(s) " + ", ".join(markers) + " in " + " and ".join(source)
                + "; such markers may be stale or intentional.",
                commits=[commit],
                filenames=commit.get("filenames", []),
                date=date_only(commit.get("date")),
                url=commit.get("url", ""),
                body="Treat this as a prompt to inspect the linked commit, not a claim that work remains.",
            ))

    if not findings:
        findings.append(_finding(
            "No open-work signal found",
            "No open issues, pull requests, or commit-based signals detected in retrieved records",
            "This MVP checks a limited set of GitHub activity. Absence of a signal does not prove the project is complete.",
            body="Work may exist in branches, commits, or other sources not covered by this analysis.",
        ))
    return findings


def _markdown_text(value):
    return re.sub(r"([\\`*_{}\[\]()#+.!|>])", r"\\\1", str(value))


def report(meta, findings, commits):
    lines = [
        f"# IntentTrail Handover Report: {_markdown_text(meta.get('full_name', ''))}", "",
        "> Rule-based clues only. These are not verified intentions; a human maintainer must review before sharing.", "",
        "## Repository overview",
        f"- Repository: {meta.get('html_url', '')}",
        f"- Description: {_markdown_text(meta.get('description') or 'Not provided')}",
        f"- Default branch: {_markdown_text(meta.get('default_branch', 'Unknown'))}",
        f"- Commits with summaries retrieved: {len(commits)}",
        f"- Commits with details retrieved: {sum(bool(commit.get('detail_available')) for commit in commits)}",
        f"- Commits excluded as generated/environment-heavy: {sum(bool(commit.get('environment_heavy')) for commit in commits)}", "",
        "## Potential unfinished work and work themes", "",
    ]
    for index, finding in enumerate(findings, 1):
        lines += [
            f"### {index}. {_markdown_text(finding['title'])}",
            f"- Signal: {_markdown_text(finding['kind'])}",
            f"- Date: {_markdown_text(finding['date'])}",
            f"- Why surfaced: {_markdown_text(finding['why'])}",
            f"- Description: {_markdown_text(finding['body'])}",
        ]
        if finding.get("commit_messages"):
            lines.append("- Supporting commit messages:")
            lines.extend(f"  - {_markdown_text(message)}" for message in finding["commit_messages"])
        else:
            lines.append("- Supporting commit messages: No commit evidence available for this issue/PR or summary signal.")
        if finding.get("filenames"):
            lines.append("- Relevant filenames:")
            lines.extend(f"  - `{_markdown_text(filename)}`" for filename in finding["filenames"])
        else:
            lines.append("- Relevant filenames: None available.")
        if finding.get("commit_urls"):
            lines.append("- Source commits:")
            lines.extend(f"  - {url}" for url in finding["commit_urls"])
        else:
            lines.append("- Source commits: None available.")
        if finding.get("url"):
            lines.append(f"- Source evidence: {finding['url']}")
        lines.append(f"- Human verification: {finding['disclaimer']}")
        lines.append("")

    lines += ["## Retrieved commit changes", ""]
    for index, commit in enumerate(commits, 1):
        lines += [
            f"### {index}. {_markdown_text(commit['message'].splitlines()[0])}",
            f"- Author: {_markdown_text(commit['author'])}",
            f"- Date: {date_only(commit['date'])}",
            f"- Commit: {commit['url'] or 'URL unavailable'}",
        ]
        if commit["detail_available"]:
            lines += [
                f"- Changed files: {commit['file_count']}",
                f"- Additions / deletions: {commit['additions']} / {commit['deletions']}",
                f"- Patch data: available for {commit['patch_count']} file(s); "
                f"{commit['patch_chars_scanned']} characters scanned"
                + (" (scan truncated)." if commit["patch_truncated"] else "."),
                "- Raw patch text is not included in this report.",
            ]
            if commit.get("environment_heavy"):
                lines.append(
                    f"- Excluded from work-theme analysis: {commit.get('generated_file_count', 0)} of "
                    f"{commit['file_count']} files are generated/environment files."
                )
            if commit["filenames"]:
                lines.append("- Filenames:")
                lines.extend(f"  - `{_markdown_text(filename)}`" for filename in commit["filenames"])
            if commit["filenames_truncated"]:
                lines.append(f"  - Additional filenames omitted (showing {len(commit['filenames'])} analyzed files).")
            lines.append(
                "- Added-line markers: " + (", ".join(commit["patch_markers"]) if commit["patch_markers"] else "None detected")
            )
        else:
            lines.append(f"- Detail status: unavailable — {_markdown_text(commit['detail_error'])}")
        lines.append("")

    lines += [
        "## Limitations",
        f"- At most {COMMIT_LIMIT} recent commits are summarized; individual details are fetched for each when the API allows.",
        f"- Diff scanning is limited to {MAX_DIFF_CHARS_PER_COMMIT:,} characters per commit; raw diff content is never displayed or included.",
        "- Binary or large files may not include patch data, and GitHub limits the files returned for a commit.",
        "- GitHub API failures or rate limits can leave a commit summary without detail; unavailable details are identified above.",
        "- Grouping uses shared filenames, parent directories, and non-generic message keywords; groups are clues, not verified themes.",
        "- Generated and environment paths (.venv, venv, site-packages, __pycache__, .pyc) are excluded from theme grouping and marker scanning; those commits stay listed for auditability.",
        "- This version uses transparent rules, not a trained ML model.",
        "- Only up to 100 issues and 100 pull requests are retrieved from their respective endpoints.",
        "- A human maintainer should verify every finding before sharing this handover.",
        "", f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
    ]
    return "\n".join(lines)


def _render_signal(finding):
    st.write(finding["body"])
    st.write("**Date:**", finding["date"])
    st.write("**Why surfaced:**", finding["why"])
    st.write("**Supporting commit messages:**")
    if finding["commit_messages"]:
        for message in finding["commit_messages"]:
            st.write(f"- {message}")
    else:
        st.caption("No commit messages are available for this issue/PR signal.")
    st.write("**Relevant filenames:**")
    if finding["filenames"]:
        for filename in finding["filenames"]:
            st.code(filename, language=None)
    else:
        st.caption("No filenames are available.")
    st.write("**Source commits:**")
    if finding["commit_urls"]:
        for url in finding["commit_urls"]:
            st.markdown(f"[View source commit]({url})")
    else:
        st.caption("No supporting commit links are available.")
    if finding["url"]:
        st.markdown(f"[View issue or source evidence]({finding['url']})")
    st.caption(finding["disclaimer"])


def _render_commit(commit):
    st.write("**Message:**")
    st.write(commit["message"])
    st.write("**Author:**", commit["author"])
    st.write("**Date:**", date_only(commit["date"]))
    if commit["detail_available"]:
        st.write(
            f"**Changed files:** {commit['file_count']} | "
            f"**Additions:** {commit['additions']} | **Deletions:** {commit['deletions']}"
        )
        st.write(
            f"**Patch data:** available for {commit['patch_count']} file(s); "
            f"{commit['patch_chars_scanned']:,} characters scanned"
            + ("; scan truncated." if commit["patch_truncated"] else ".")
        )
        st.caption("Raw patch text is intentionally not displayed.")
        if commit["filenames"]:
            st.write("**Changed filenames:**")
            for filename in commit["filenames"]:
                st.code(filename, language=None)
        if commit.get("environment_heavy"):
            st.warning(
                f"Excluded from work-theme analysis: {commit.get('generated_file_count', 0)} of "
                f"{commit['file_count']} files are generated/environment files."
            )
        if commit["filenames_truncated"]:
            st.caption(
                f"Showing {len(commit['filenames'])} analyzed filenames; additional filenames omitted."
            )
        if commit["patch_markers"]:
            st.write("**Added-line markers:**", ", ".join(commit["patch_markers"]))
    else:
        st.warning(commit["detail_error"])
    if commit["url"]:
        st.markdown(f"[Open commit on GitHub]({commit['url']})")



def _build_round3_context(meta, commits, issues, pulls, project_goal=""):
    """Build the evidence graph and bounded model payload for one repository run."""
    graph = round3_engine.build_relationship_graph(commits, issues, pulls)
    payload = round3_engine.build_intent_payload(
        meta.get("full_name", ""), commits, issues, pulls, graph=graph,
        project_goal=project_goal,
    )
    return graph, payload


def _hypothesis_to_finding(hypothesis):
    """Adapt a validated model hypothesis to the shared human-review format."""
    return {
        "kind": "AI-generated hypothesis",
        "title": hypothesis.get("title") or "Possible work theme",
        "body": hypothesis.get("summary", ""),
        "summary": hypothesis.get("summary", ""),
        "date": "—",
        "url": "",
        "why": "Suggested by Groq using the evidence references listed below.",
        "commit_messages": [],
        "filenames": [],
        "commit_urls": [],
        "evidence_refs": hypothesis.get("evidence_refs", []),
        "suggested_next_steps": hypothesis.get("suggested_next_steps", []),
        "disclaimer": hypothesis.get("human_review", HUMAN_REVIEW),
    }


def _append_review_items(existing, findings):
    """Append new review items without resetting earlier human decisions."""
    items = list(existing or [])
    for finding in findings:
        item = round3_engine.make_review_items([finding])[0]
        seed = f"{len(items)}|{item['review_id']}|{finding.get('title', '')}"
        item["review_id"] = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        items.append(item)
    return items


def _render_relationship_graph(graph):
    """Render evidence-backed repository associations as a graph and detail table."""
    import hashlib
    import json

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    st.subheader("Project relationships")
    st.caption(
        "Connections are evidence-backed associations (shared files, directories, keywords, "
        "or explicit GitHub references), not verified technical dependencies."
    )
    left, right = st.columns(2)
    left.metric("Evidence nodes", len(nodes))
    right.metric("Relationships", len(edges))

    if nodes and edges:
        # Stable opaque DOT identifiers prevent repository text from becoming graph syntax.
        dot_ids = {
            node.get("id", ""): "n" + hashlib.sha1(str(node.get("id", "")).encode("utf-8")).hexdigest()[:12]
            for node in nodes
        }
        type_styles = {
            "commit": ("#DCEBFF", "#2563EB"),
            "issue": ("#DCFCE7", "#15803D"),
            "pull_request": ("#F3E8FF", "#7E22CE"),
        }
        lines = [
            "digraph IntentTrail {",
            '  graph [rankdir="LR", bgcolor="transparent", pad="0.25", nodesep="0.45", ranksep="0.8", overlap=false, splines=true];',
            '  node [shape="box", style="rounded,filled", fontname="Arial", fontsize="10", margin="0.14,0.09"];',
            '  edge [fontname="Arial", fontsize="8", color="#64748B", fontcolor="#475569", arrowsize="0.7"];',
        ]
        for node in nodes:
            node_id = node.get("id", "")
            if not node_id:
                continue
            label = f"{node.get('type', 'item').replace('_', ' ').title()}\n{node.get('label') or node_id}"
            fill, stroke = type_styles.get(node.get("type"), ("#F1F5F9", "#64748B"))
            lines.append(
                f"  {dot_ids[node_id]} [label={json.dumps(label)}, fillcolor={json.dumps(fill)}, "
                f"color={json.dumps(stroke)}, fontcolor={json.dumps('#0F172A')}];"
            )
        for edge in edges:
            source = dot_ids.get(edge.get("source"))
            target = dot_ids.get(edge.get("target"))
            if not source or not target:
                continue
            edge_label = edge.get("type", "related").replace("_", " ")
            evidence = ", ".join(edge.get("evidence", [])[:3])
            tooltip = edge.get("reason", "")
            if evidence:
                tooltip = f"{tooltip} Evidence: {evidence}"
            lines.append(
                f"  {source} -> {target} [label={json.dumps(edge_label)}, tooltip={json.dumps(tooltip)}];"
            )
        lines.append("}")
        st.graphviz_chart("\n".join(lines), use_container_width=True)
        rows = []
        labels = {node.get("id"): f"{node.get('type', 'item')}: {node.get('label', node.get('id'))}" for node in nodes}
        for edge in edges:
            rows.append({
                "From": labels.get(edge.get("source"), edge.get("source", "")),
                "Relationship": edge.get("type", ""),
                "To": labels.get(edge.get("target"), edge.get("target", "")),
                "Evidence": ", ".join(edge.get("evidence", [])),
                "Why connected": edge.get("reason", ""),
            })
        with st.expander("Inspect relationship evidence", expanded=False):
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    elif nodes:
        st.info("Evidence records were found, but no relationships connected them in this retrieval window.")
        st.caption("No lines are drawn because the engine did not find evidence for a connection.")
        st.dataframe(pd.DataFrame([
            {"Type": n.get("type", "item"), "Record": n.get("label", n.get("id", "")), "Source": n.get("url", "")}
            for n in nodes
        ]), use_container_width=True, hide_index=True)
    else:
        st.info("No relationship records were found in the retrieved data. This does not mean no dependencies exist.")

    for limitation in graph.get("limitations", []):
        st.caption(f"Limitation: {limitation}")



def _render_reconstruction(reconstruction, payload):
    if not reconstruction:
        return
    st.header("🧩 Reconstructed missing development intent")
    st.write(reconstruction.get("likely_direction") or "No clear direction could be reconstructed.")
    if reconstruction.get("current_state"):
        st.markdown("**What the repository appears to contain now**")
        st.write(reconstruction["current_state"])

    evidence_by_id = {item.get("id"): item for item in payload.get("evidence", [])}

    def show_refs(refs):
        for ref in refs or []:
            item = evidence_by_id.get(ref, {})
            url = item.get("url", "")
            label = ref
            if item.get("title"):
                label = f"{ref} — {item['title']}"
            elif item.get("message"):
                label = f"{ref} — {item['message'][:100]}"
            if url.startswith("https://github.com/"):
                st.markdown(f"- [{label}]({url})")
            else:
                st.markdown(f"- {label}")

    gaps = reconstruction.get("missing_gaps", [])
    if gaps:
        st.markdown("**Possible missing pieces**")
        for gap in gaps:
            with st.container(border=True):
                st.write(gap.get("description", ""))
                show_refs(gap.get("evidence_refs"))

    paths = reconstruction.get("next_paths", [])
    if paths:
        st.markdown("**Likely next development directions**")
        for path in paths:
            with st.container(border=True):
                st.markdown(f"**{path.get('title', 'Possible next path')}** · {path.get('confidence', 'low').title()} model-assessed confidence")
                st.write(path.get("reasoning", ""))
                for step in path.get("suggested_next_steps", []):
                    st.markdown(f"- {step}")
                show_refs(path.get("evidence_refs"))
    st.caption(reconstruction.get("disclaimer", "This is an evidence-backed hypothesis, not a verified intention."))
    if paths:
        st.caption("Confidence labels are qualitative model assessments, not calibrated probabilities.")


def _render_next_work_path(commits, issues, pulls, graph, groq_result=None):
    """Show ranked, evidence-linked recommendations as the primary product output."""
    hypotheses = (groq_result or {}).get("hypotheses", []) if (groq_result or {}).get("status") == "success" else []
    reconstruction = (groq_result or {}).get("reconstruction") or {}
    for path in reconstruction.get("next_paths", []):
        hypotheses.append({
            "title": path.get("title", "Possible next path"),
            "summary": path.get("reasoning", "Goal-aware path reconstructed from repository evidence."),
            "suggested_next_steps": path.get("suggested_next_steps", []),
            "evidence_refs": path.get("evidence_refs", []),
        })
    predictions = round3_engine.build_next_work_path(
        commits, issues, pulls, graph=graph, hypotheses=hypotheses, limit=6
    )
    st.header("🧭 Predicted next work path")
    st.write(
        "A ranked set of plausible next actions informed by the project goal, reconstructed missing intent, "
        "open GitHub work, follow-up markers, and linked repository activity."
    )
    st.caption(
        "Priority scores are transparent heuristics, not probabilities. Every recommendation "
        "needs developer verification; repository evidence cannot reveal private intent."
    )
    if not predictions:
        st.info(
            "No strong next-work signal was found in the retrieved commits, issues, or pull requests. "
            "Inspect the latest changes and check branches or project discussions not included in this analysis."
        )
        return

    report_lines = [
        "# IntentTrail: Predicted next work path", "",
        "> Priority scores are heuristic rankings, not probabilities. Verify all source evidence before acting.", "",
    ]
    for index, item in enumerate(predictions, 1):
        report_lines.extend([
            f"## {index}. {item['title']}", "",
            f"- Category: {item['category']}",
            f"- Priority: {item['priority_label']} ({item['priority_score']}/100)",
            f"- Evidence basis: {item['basis']}", "", item['summary'], "",
            "### Suggested next steps", *[f"- {step}" for step in item['suggested_next_steps']], "",
            "### Source evidence", *[
                f"- [{e.get('label') or e.get('id')}]({e.get('url')})" if e.get('url') else f"- {e.get('label') or e.get('id')}"
                for e in item['evidence']
            ], "", item['disclaimer'], "",
        ])
        with st.container(border=True):
            left, right = st.columns([5, 1])
            left.markdown(f"**{index}. {item['title']}**")
            left.caption(f"{item['category']} · {item['priority_label']} priority")
            right.metric("Priority", f"{item['priority_score']}/100")
            st.write(item["summary"])
            with st.expander("Why this is recommended and what to do next", expanded=(index == 1)):
                st.write(f"**Evidence basis:** {item['basis']}")
                st.write("**Suggested next steps**")
                for step in item["suggested_next_steps"]:
                    st.markdown(f"- {step}")
                st.write("**Source evidence**")
                for evidence in item["evidence"]:
                    if evidence.get("url", "").startswith("https://github.com/"):
                        st.markdown(f"- [{evidence.get('label') or evidence.get('id')}]({evidence['url']})")
                    else:
                        st.markdown(f"- {evidence.get('label') or evidence.get('id')}")
                st.caption(item["disclaimer"])
    st.download_button(
        "Download predicted work path (Markdown)",
        "\n".join(report_lines),
        file_name="intenttrail_next_work_path.md",
        mime="text/markdown",
        key="next_work_path_download",
    )


def _render_human_review(meta, commits, graph, issues=None, pulls=None):
    review_items = st.session_state.get("review_items", [])
    st.subheader("Human review and role-specific handover")
    st.caption("Only findings you explicitly approve are included in role-specific handover exports.")
    if not review_items:
        st.info("There are no findings to review yet.")
        return

    labels = [
        f"[{item.get('status', 'pending').upper()}] {item.get('edited_title') or item['original'].get('title', 'Untitled')}"
        for item in review_items
    ]
    selected_index = st.selectbox(
        "Choose a finding to review", range(len(review_items)),
        format_func=lambda index: labels[index], key="review_selection",
    )
    selected = review_items[selected_index]
    original = selected.get("original", {})
    st.write(original.get("body") or original.get("summary") or original.get("why", "No summary provided."))
    if original.get("evidence_refs"):
        st.write("**Evidence references:** " + ", ".join(original["evidence_refs"]))
    elif original.get("url"):
        st.markdown(f"[Open source evidence]({original['url']})")
    for commit_url in original.get("commit_urls", []):
        st.markdown(f"[Open supporting commit]({commit_url})")

    edited_title = st.text_input("Reviewed title", value=selected.get("edited_title", ""), key=f"review_title_{selected['review_id']}")
    reviewer_note = st.text_area("Reviewer note", value=selected.get("reviewer_note", ""), key=f"review_note_{selected['review_id']}")
    current_status = selected.get("status", "pending")
    st.caption(f"Current decision: {current_status}")
    approve_col, reject_col, pending_col = st.columns(3)
    if approve_col.button("Approve finding", type="primary", key=f"approve_{selected['review_id']}"):
        st.session_state["review_items"] = round3_engine.update_review_item(
            review_items, selected["review_id"], status="approved",
            edited_title=edited_title, reviewer_note=reviewer_note,
        )
        st.rerun()
    if reject_col.button("Reject finding", key=f"reject_{selected['review_id']}"):
        st.session_state["review_items"] = round3_engine.update_review_item(
            review_items, selected["review_id"], status="rejected",
            edited_title=edited_title, reviewer_note=reviewer_note,
        )
        st.rerun()
    if pending_col.button("Reset to pending", key=f"pending_{selected['review_id']}"):
        st.session_state["review_items"] = round3_engine.update_review_item(
            review_items, selected["review_id"], status="pending",
            edited_title=edited_title, reviewer_note=reviewer_note,
        )
        st.rerun()

    approved = round3_engine.approved_findings(review_items)
    st.write(f"**Approved findings:** {len(approved)} / {len(review_items)}")
    role = st.selectbox(
        "Handover audience", ["developer", "maintainer", "project_lead"],
        format_func=lambda value: {
            "developer": "Incoming developer",
            "maintainer": "Repository maintainer",
            "project_lead": "Project lead",
        }[value], key="handover_role",
    )
    role_report = round3_engine.role_handover_report(
        meta.get("full_name", "repository"), role, approved,
        commits=commits, graph=graph, issues=issues, pulls=pulls,
    )
    st.download_button(
        "Download approved role handover (Markdown)", role_report,
        file_name=f"intenttrail_{role}_handover.md", mime="text/markdown",
        key="role_handover_download",
    )


def render_app():
    st.set_page_config(page_title="IntentTrail", page_icon="🧭", layout="wide")
    st.title("🧭 IntentTrail")
    st.subheader("Don't just inherit the code. Understand the journey behind it.")
    st.write("Compare a project's intended goal with scattered repository evidence to reconstruct the missing development direction and predict what should happen next.")
    st.info("Public GitHub repositories only. Intent reconstruction is an evidence-backed hypothesis, not access to a developer's private thoughts.")

    with st.form("repo_form"):
        url = st.text_input("Public GitHub repository URL", placeholder="https://github.com/owner/repo")
        go = st.form_submit_button("Analyze repository", type="primary")

    if go:
        try:
            owner, repo = parse_repo_url(url)
            with st.spinner("Fetching GitHub activity and commit changes..."):
                meta, commits, issues, pulls = fetch(owner, repo)
                findings = analyze(issues, pulls, commits)
            st.session_state["result"] = (meta, commits, issues, pulls, findings)
            st.session_state["project_goal_editor"] = _draft_project_goal(meta)
            graph, intent_payload = _build_round3_context(meta, commits, issues, pulls)
            st.session_state["round3_graph"] = graph
            st.session_state["intent_payload"] = intent_payload
            st.session_state["groq_result"] = None
            st.session_state["goal_reconstruction_payload"] = None
            st.session_state["review_items"] = round3_engine.make_review_items(findings)
            st.success(f"Analysis complete: {meta.get('full_name')}")
        except requests.exceptions.Timeout:
            st.error("GitHub request timed out. Try again.")
        except requests.exceptions.ConnectionError:
            st.error("Could not connect. Check your internet connection.")
        except requests.exceptions.HTTPError as error:
            st.error(f"GitHub API error: {error}")
        except requests.exceptions.RequestException:
            st.error("GitHub API request failed. Check your connection and try again.")
        except (ValueError, RuntimeError) as error:
            st.error(str(error))

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
        st.caption(
            f"Default branch: {meta.get('default_branch')} | "
            f"Stars: {meta.get('stargazers_count')} | Forks: {meta.get('forks_count')}"
        )

        st.divider()
        st.header("Round 3: Intent reconstruction and handover")
        st.write(
            "Start with the project's intended destination, then compare it with repository evidence "
            "to reconstruct the likely missing development direction."
        )
        st.subheader("1. Project goal / original problem statement")
        st.caption("A draft is taken from the repository README or description. Correct it to match the original project goal before running reconstruction.")
        if "project_goal_editor" not in st.session_state:
            st.session_state["project_goal_editor"] = _draft_project_goal(meta)
        project_goal = st.text_area(
            "What is this project ultimately trying to achieve?",
            key="project_goal_editor", height=110,
            placeholder="Describe the original problem, intended outcome, or feature direction...",
        )
        if st.button("Reconstruct missing development intent", type="primary", key="run_groq_analysis"):
            if not project_goal.strip():
                st.warning("Add the project goal or problem statement first. You can use the README draft as a starting point.")
            else:
                with st.spinner("Comparing the project goal with repository evidence..."):
                    goal_payload = round3_engine.build_intent_payload(
                        meta.get("full_name", ""), commits, issues, pulls,
                        graph=st.session_state.get("round3_graph", {}),
                        project_goal=project_goal,
                    )
                    groq_result = round3_engine.analyze_with_groq(
                        goal_payload, api_key=os.getenv("GROQ_API_KEY")
                    )
                st.session_state["intent_payload"] = goal_payload
                st.session_state["groq_result"] = groq_result
                st.session_state["goal_reconstruction_payload"] = goal_payload
                if groq_result.get("status") == "success":
                    new_findings = [
                        _hypothesis_to_finding(item)
                        for item in groq_result.get("hypotheses", [])
                    ]
                    reconstruction = groq_result.get("reconstruction") or {}
                    if reconstruction.get("likely_direction") and reconstruction.get("evidence_refs"):
                        new_findings.append(_hypothesis_to_finding({
                            "title": "Reconstructed direction: " + reconstruction["likely_direction"][:120],
                            "summary": reconstruction.get("current_state", ""),
                            "evidence_refs": reconstruction.get("evidence_refs", []),
                            "suggested_next_steps": [],
                        }))
                    for gap in reconstruction.get("missing_gaps", []):
                        new_findings.append(_hypothesis_to_finding({
                            "title": "Possible missing piece: " + gap.get("description", "")[:120],
                            "summary": gap.get("description", ""),
                            "evidence_refs": gap.get("evidence_refs", []),
                        }))
                    for path in reconstruction.get("next_paths", []):
                        new_findings.append(_hypothesis_to_finding({
                            "title": path.get("title", "Possible next path"),
                            "summary": path.get("reasoning", ""),
                            "evidence_refs": path.get("evidence_refs", []),
                            "suggested_next_steps": path.get("suggested_next_steps", []),
                        }))
                    if new_findings:
                        st.session_state["review_items"] = _append_review_items(
                            st.session_state.get("review_items", []), new_findings
                        )
        groq_result = st.session_state.get("groq_result")
        if groq_result:
            if groq_result.get("status") == "success":
                st.success(groq_result.get("message", "Intent reconstruction completed."))
                st.caption(f"Model: {groq_result.get('model') or 'Not reported'}")
                if not groq_result.get("reconstruction"):
                    st.info("The model did not return a reconstruction with verifiable evidence references. Check the goal and repository evidence, or try again later.")
                _render_reconstruction(
                    groq_result.get("reconstruction"),
                    st.session_state.get("goal_reconstruction_payload", st.session_state.get("intent_payload", {})),
                )
                if not groq_result.get("hypotheses") and not groq_result.get("reconstruction"):
                    st.info("No evidence-supported reconstruction or hypotheses were returned. Rule-based recommendations remain available.")
            else:
                st.warning(groq_result.get("message", "Groq is unavailable; rule-based analysis remains active."))
        else:
            st.caption("Intent reconstruction has not been run. Rule-based relationship mapping and next-work recommendations remain available.")

        _render_relationship_graph(st.session_state.get("round3_graph", {}))
        _render_next_work_path(
            commits, issues, pulls,
            st.session_state.get("round3_graph", {}),
            st.session_state.get("groq_result"),
        )

        st.subheader("Potential unfinished work and work themes")
        st.warning("These are rule-based signals, not verified intentions. Human verification is required before sharing.")
        for index, finding in enumerate(findings, 1):
            with st.expander(f"{index}. [{finding['kind']}] {finding['title']}"):
                _render_signal(finding)

        failed_details = sum(not commit["detail_available"] for commit in commits)
        if failed_details:
            st.warning(
                f"Commit details were unavailable for {failed_details} of {len(commits)} commits. "
                "Open those commits on GitHub to inspect their changes."
            )

        st.subheader("Recent commits and change summary")
        st.caption(
            "Up to 30 commits are detailed. Diff text is not shown; scanning is bounded and reports only marker names."
        )
        rows = []
        for commit in commits:
            rows.append({
                "Date": date_only(commit["date"]),
                "Message": commit["message"].splitlines()[0],
                "Author": commit["author"],
                "Files": commit["file_count"] if commit["detail_available"] else "Unavailable",
                "Additions": commit["additions"] if commit["detail_available"] else "Unavailable",
                "Deletions": commit["deletions"] if commit["detail_available"] else "Unavailable",
                "Analysis": (
                    "Excluded (generated files)" if commit.get("environment_heavy")
                    else "Included"
                ) if commit["detail_available"] else "Unavailable",
                "Patch": (
                    "Available" if commit["patch_count"] and not commit["patch_truncated"]
                    else "Limited" if commit["patch_count"]
                    else "Unavailable"
                ) if commit["detail_available"] else "Unavailable",
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            for index, commit in enumerate(commits, 1):
                message = commit["message"].splitlines()[0]
                with st.expander(f"{index}. {message[:100]}"):
                    _render_commit(commit)
        else:
            st.write("No commits returned.")

        _render_human_review(meta, commits, st.session_state.get("round3_graph", {}), issues, pulls)

        st.download_button(
            "Download handover report (Markdown)",
            report(meta, findings, commits),
            file_name=f"intenttrail_{meta.get('name', 'repo')}_handover.md",
            mime="text/markdown",
            type="primary",
        )
        st.caption("Human review is required before sharing this handover.")

    st.divider()
    st.caption("IntentTrail MVP | GitHub-first | Evidence-aware | Human-reviewed")


if __name__ == "__main__":
    render_app()
