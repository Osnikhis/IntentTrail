import unittest
from unittest.mock import patch

import app


class CommitExtractionTests(unittest.TestCase):
    def test_api_get_recognizes_secondary_rate_limit(self):
        response = app.requests.Response()
        response.status_code = 403
        response.headers["X-RateLimit-Remaining"] = "10"
        response._content = b'{"message":"You have exceeded a secondary rate limit."}'
        with patch("app.requests.get", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "rate limit"):
                app.api_get("/repos/acme/demo")

    def test_fetch_extracts_commit_details_without_retaining_patch_text(self):
        summary = {
            "sha": "abc123",
            "html_url": "https://github.com/acme/demo/commit/abc123",
            "commit": {
                "message": "WIP: finish login",
                "author": {"name": "Ada", "date": "2026-01-02T03:04:05Z"},
            },
        }
        detail = {
            "sha": "abc123",
            "html_url": summary["html_url"],
            "author": {"login": "ada"},
            "commit": {
                "message": "WIP: finish login",
                "author": {"name": "Ada", "date": "2026-01-02T03:04:05Z"},
            },
            "stats": {"additions": 4, "deletions": 2},
            "files": [{
                "filename": "src/login.py",
                "patch": "@@ -1 +1 @@\n+ # TODO: TOPSECRET_VALUE",
            }],
        }
        with patch("app.api_get", side_effect=[
            {"full_name": "acme/demo"},
            [summary],
            [],
            [],
            detail,
        ]) as api_call:
            meta, commits, issues, pulls = app.fetch("acme", "demo")

        self.assertEqual(meta["full_name"], "acme/demo")
        self.assertEqual((len(issues), len(pulls)), (0, 0))
        self.assertEqual(api_call.call_args_list[1].args[0], "/repos/acme/demo/commits")
        self.assertEqual(commits[0]["author"], "ada")
        self.assertEqual(commits[0]["filenames"], ["src/login.py"])
        self.assertEqual((commits[0]["additions"], commits[0]["deletions"]), (4, 2))
        self.assertEqual(commits[0]["patch_markers"], ["TODO"])
        self.assertNotIn("TOPSECRET_VALUE", repr(commits[0]))

    def test_diff_scan_is_bounded_and_reports_truncation(self):
        files = [{
            "filename": "large.py",
            "patch": "+" + ("a" * app.MAX_DIFF_CHARS_PER_COMMIT) + "\n+TODO",
        }]

        markers, patch_count, scanned, truncated = app._scan_patch_files(files)

        self.assertEqual(patch_count, 1)
        self.assertEqual(scanned, app.MAX_DIFF_CHARS_PER_COMMIT)
        self.assertTrue(truncated)
        self.assertEqual(markers, [])

    def test_missing_patch_is_reported_without_failure(self):
        self.assertEqual(app._scan_patch_files([{"filename": "image.png"}]), ([], 0, 0, False))

    def test_rate_limit_keeps_commit_summary_and_marks_following_details_unavailable(self):
        summaries = [
            {"sha": "one", "commit": {"message": "First"}},
            {"sha": "two", "commit": {"message": "Second"}},
        ]
        with patch("app.api_get", side_effect=[
            {"full_name": "acme/demo"},
            summaries,
            [],
            [],
            RuntimeError("GitHub API rate limit reached. Wait and try again."),
        ]):
            _, commits, _, _ = app.fetch("acme", "demo")

        self.assertEqual([commit["message"] for commit in commits], ["First", "Second"])
        self.assertTrue(all(not commit["detail_available"] for commit in commits))
        self.assertIn("rate limit", commits[0]["detail_error"].lower())
        self.assertIn("rate limit", commits[1]["detail_error"].lower())

    def test_server_error_on_one_commit_does_not_hide_other_details(self):
        response = app.requests.Response()
        response.status_code = 500
        server_error = app.requests.exceptions.HTTPError("server error", response=response)
        summaries = [
            {"sha": "one", "commit": {"message": "First"}},
            {"sha": "two", "commit": {"message": "Second"}},
        ]
        detail = {
            "sha": "two",
            "commit": {"message": "Second", "author": {"name": "Ada"}},
            "stats": {"additions": 1, "deletions": 0},
            "files": [],
        }
        with patch("app.api_get", side_effect=[
            {"full_name": "acme/demo"},
            summaries,
            [],
            [],
            server_error,
            detail,
        ]):
            _, commits, _, _ = app.fetch("acme", "demo")

        self.assertFalse(commits[0]["detail_available"])
        self.assertIn("HTTP 500", commits[0]["detail_error"])
        self.assertTrue(commits[1]["detail_available"])


class SignalTests(unittest.TestCase):
    def test_related_commits_are_grouped_with_explainable_reason(self):
        commits = [
            {"message": "Add login validation", "filenames": ["src/auth/login.py"]},
            {"message": "Improve login handling", "filenames": ["src/auth/login.py"]},
            {"message": "Update unrelated docs", "filenames": ["docs/guide.md"]},
        ]

        groups = app.group_related_commits(commits)

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["commits"]), 2)
        self.assertTrue(any("shared filename" in reason for reason in groups[0]["reasons"]))

    def test_commit_signal_includes_evidence_and_report_excludes_diff_text(self):
        commit = {
            "message": "WIP: finish login",
            "author": "Ada",
            "date": "2026-01-02T03:04:05Z",
            "url": "https://github.com/acme/demo/commit/abc123",
            "filenames": ["src/login.py"],
            "patch_markers": ["TODO"],
            "detail_available": True,
            "file_count": 1,
            "filenames_truncated": False,
            "additions": 1,
            "deletions": 0,
            "patch_count": 1,
            "patch_chars_scanned": 18,
            "patch_truncated": False,
        }

        findings = app.analyze([], [], [commit])
        report = app.report({"full_name": "acme/demo"}, findings, [commit])

        signal = next(item for item in findings if item["kind"] == "Possible unfinished work")
        self.assertEqual(signal["commit_messages"], ["WIP: finish login"])
        self.assertEqual(signal["filenames"], ["src/login.py"])
        self.assertEqual(signal["commit_urls"], [commit["url"]])
        self.assertIn("human maintainer must verify", signal["disclaimer"])
        self.assertIn("Supporting commit messages", report)
        self.assertIn(commit["url"], report)
        self.assertIn("Raw patch text is not included", report)
        self.assertNotIn("TOPSECRET_VALUE", report)


class GeneratedFileFilteringTests(unittest.TestCase):
    def test_generated_paths_are_detected(self):
        self.assertTrue(app.is_generated_path(".venv/Lib/site-packages/PIL/Image.py"))
        self.assertTrue(app.is_generated_path("pkg/__pycache__/module.cpython-311.pyc"))
        self.assertTrue(app.is_generated_path("venv\\Lib\\site-packages\\x.py"))
        self.assertFalse(app.is_generated_path("app.py"))
        self.assertFalse(app.is_generated_path(".gitignore"))
        self.assertFalse(app.is_generated_path("src/auth/login.py"))

    def test_environment_heavy_commit_is_flagged_and_not_scanned(self):
        files = [
            {"filename": f".venv/Lib/site-packages/PIL/m{i}.py", "patch": "+TODO"}
            for i in range(9)
        ]
        files.append({"filename": ".gitignore", "patch": "+.venv/"})
        detail = {
            "commit": {"message": "Initial MVP"},
            "files": files,
            "stats": {"additions": 10, "deletions": 0},
            "html_url": "https://github.com/acme/demo/commit/abc123",
        }
        with patch("app.api_get", return_value=detail):
            commit = app._enrich_commit("acme", "demo", {"sha": "abc123"})

        self.assertTrue(commit["environment_heavy"])
        self.assertEqual(commit["file_count"], 10)
        self.assertEqual(commit["generated_file_count"], 9)
        self.assertEqual(commit["filenames"], [".gitignore"])
        self.assertEqual(commit["patch_markers"], [])

    def test_environment_heavy_commits_are_not_grouped(self):
        first = {"sha": "1", "message": "Initial MVP", "filenames": [".gitignore"], "environment_heavy": True}
        second = {"sha": "2", "message": "Remove environment", "filenames": [".gitignore"], "environment_heavy": True}

        self.assertEqual(app.group_related_commits([first, second]), [])

    def test_report_lists_excluded_commit_for_auditability(self):
        commit = {
            "message": "Initial MVP",
            "author": "Ada",
            "date": "2026-01-02T03:04:05Z",
            "url": "https://github.com/acme/demo/commit/abc123",
            "filenames": [".gitignore"],
            "patch_markers": [],
            "detail_available": True,
            "environment_heavy": True,
            "generated_file_count": 9,
            "file_count": 10,
            "filenames_truncated": False,
            "additions": 10,
            "deletions": 0,
            "patch_count": 1,
            "patch_chars_scanned": 7,
            "patch_truncated": False,
        }

        report = app.report({"full_name": "acme/demo"}, [], [commit])

        self.assertIn("Initial MVP", report)
        self.assertIn("Excluded from work-theme analysis", report)


if __name__ == "__main__":
    unittest.main()


class Round3IntegrationHelperTests(unittest.TestCase):
    def test_round3_context_builds_graph_and_payload_without_patch_text(self):
        meta = {"full_name": "acme/demo"}
        commits = [{
            "sha": "abc", "message": "Add login", "filenames": ["src/login.py"],
            "patch": "+password=should-not-leak", "url": "https://github.com/acme/demo/commit/abc",
        }]
        graph, payload = app._build_round3_context(meta, commits, [], [])
        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(payload["repository"], "acme/demo")
        self.assertNotIn("'patch':", repr(payload))
        self.assertNotIn("should-not-leak", repr(payload))

    def test_hypothesis_is_adapted_to_human_review_format(self):
        finding = app._hypothesis_to_finding({
            "title": "Authentication follow-up", "summary": "Review login changes.",
            "evidence_refs": ["commit:abc"], "suggested_next_steps": ["Run auth tests."],
            "human_review": "Human review required.",
        })
        self.assertEqual(finding["kind"], "AI-generated hypothesis")
        self.assertEqual(finding["evidence_refs"], ["commit:abc"])
        self.assertEqual(finding["suggested_next_steps"], ["Run auth tests."])

    def test_appending_ai_review_does_not_reset_existing_decisions(self):
        initial = app.round3_engine.make_review_items([{"kind": "Rule", "title": "Existing"}])
        initial = app.round3_engine.update_review_item(
            initial, initial[0]["review_id"], status="approved", reviewer_note="Verified"
        )
        updated = app._append_review_items(initial, [{"kind": "AI", "title": "New hypothesis"}])
        self.assertEqual(updated[0]["status"], "approved")
        self.assertEqual(updated[0]["reviewer_note"], "Verified")
        self.assertEqual(len(updated), 2)
        self.assertNotEqual(updated[0]["review_id"], updated[1]["review_id"])
