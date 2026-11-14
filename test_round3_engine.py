import json
import unittest
from unittest.mock import Mock, patch

import round3_engine as engine


class RelationshipGraphTests(unittest.TestCase):
    def test_shared_file_creates_explainable_commit_edge(self):
        commits = [
            {"sha": "a1", "message": "Add login validation", "filenames": ["src/auth/login.py"]},
            {"sha": "b2", "message": "Improve login handling", "filenames": ["src/auth/login.py"]},
        ]
        graph = engine.build_relationship_graph(commits, [], [])
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertTrue(any(edge["type"] == "shared_filename" for edge in graph["edges"]))

    def test_commit_reference_links_to_issue(self):
        commits = [{"sha": "abc", "message": "Fix validation for #12", "filenames": []}]
        issues = [{"number": 12, "title": "Validation bug", "body": "", "html_url": "https://github.com/x/y/issues/12"}]
        graph = engine.build_relationship_graph(commits, issues, [])
        self.assertTrue(any(edge["type"] == "explicit_github_reference" for edge in graph["edges"]))

    def test_environment_heavy_commits_are_not_linked_by_file(self):
        commits = [
            {"sha": "a", "message": "Dependency dump", "filenames": ["requirements.txt"], "environment_heavy": True},
            {"sha": "b", "message": "Update dependencies", "filenames": ["requirements.txt"], "environment_heavy": True},
        ]
        graph = engine.build_relationship_graph(commits, [], [])
        self.assertEqual(graph["edges"], [])


class IntentPayloadTests(unittest.TestCase):
    def test_payload_omits_raw_patch_and_redacts_secrets(self):
        commits = [{
            "sha": "abc", "message": "WIP token=supersecretvalue",
            "filenames": ["src/app.py"], "patch": "+password=hunter2",
            "url": "https://github.com/x/y/commit/abc",
        }]
        payload = engine.build_intent_payload("x/y", commits, [], [])
        encoded = json.dumps(payload)
        self.assertNotIn("supersecretvalue", encoded)
        self.assertNotIn("hunter2", encoded)
        self.assertNotIn('"patch"', encoded)

    def test_no_key_uses_fallback(self):
        payload = engine.build_intent_payload("x/y", [], [], [])
        with patch.dict("os.environ", {}, clear=True):
            result = engine.analyze_with_groq(payload, api_key="")
        self.assertEqual(result["status"], "fallback")
        self.assertEqual(result["hypotheses"], [])

    def test_project_goal_is_included_in_bounded_payload(self):
        payload = engine.build_intent_payload(
            "x/y", [], [], [], project_goal="Build a beginner-friendly learning platform"
        )
        self.assertEqual(payload["project_goal"], "Build a beginner-friendly learning platform")

    def test_reconstruction_rejects_unverified_evidence_references(self):
        allowed = {"commit:abc", "issue:7"}
        result = engine._validate_reconstruction({
            "likely_direction": "Complete login validation",
            "current_state": "Login exists",
            "evidence_refs": ["commit:abc", "commit:invented"],
            "missing_gaps": [
                {"description": "Validation tests may be incomplete", "evidence_refs": ["issue:7"]},
                {"description": "Unsupported claim", "evidence_refs": ["issue:999"]},
            ],
            "next_paths": [
                {"title": "Add validation tests", "reasoning": "Issue is open",
                 "confidence": "medium", "suggested_next_steps": ["Inspect test suite"],
                 "evidence_refs": ["issue:7"]},
                {"title": "Invented path", "evidence_refs": ["commit:fake"]},
            ],
        }, allowed)
        self.assertEqual(result["evidence_refs"], ["commit:abc"])
        self.assertEqual(len(result["missing_gaps"]), 1)
        self.assertEqual(len(result["next_paths"]), 1)
        self.assertEqual(result["next_paths"][0]["confidence"], "medium")

    def test_reconstruction_without_any_valid_evidence_is_rejected(self):
        result = engine._validate_reconstruction({
            "likely_direction": "Complete payments",
            "evidence_refs": ["commit:missing"],
            "missing_gaps": [], "next_paths": [],
        }, {"commit:real"})
        self.assertIsNone(result)


class GroqValidationTests(unittest.TestCase):
    @patch("round3_engine.requests.post")
    def test_successful_response_keeps_only_valid_evidence_ids(self, post):
        payload = engine.build_intent_payload(
            "x/y",
            [{"sha": "abc", "message": "Add login", "filenames": ["src/login.py"]}],
            [], [], project_goal="Build a secure sign-in experience",
        )
        valid_id = "commit:abc"
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"message": {"content": json.dumps({
                "reconstruction": {
                    "likely_direction": "Complete authentication validation",
                    "current_state": "Login code was added",
                    "evidence_refs": [valid_id],
                    "missing_gaps": [{"description": "Validation tests may be incomplete",
                                      "evidence_refs": [valid_id, "issue:999"]}],
                    "next_paths": [{"title": "Add auth tests", "reasoning": "Recent login work",
                                    "confidence": "medium", "suggested_next_steps": ["Inspect tests"],
                                    "evidence_refs": [valid_id]}],
                },
                "hypotheses": [
                    {"title": "Authentication work", "summary": "Related login changes.",
                     "suggested_next_steps": ["Review login tests."],
                     "evidence_refs": [valid_id, "issue:999"]}
                ]
            })}}]
        }
        post.return_value = response
        result = engine.analyze_with_groq(payload, api_key="test-key")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["hypotheses"][0]["evidence_refs"], [valid_id])
        self.assertEqual(result["reconstruction"]["evidence_refs"], [valid_id])
        self.assertEqual(len(result["reconstruction"]["missing_gaps"]), 1)
        self.assertIn("Build a secure sign-in experience", post.call_args.kwargs["json"]["messages"][1]["content"])

    @patch("round3_engine.requests.post")
    def test_rate_limit_falls_back(self, post):
        payload = engine.build_intent_payload("x/y", [], [], [])
        response = Mock()
        response.status_code = 429
        post.return_value = response
        result = engine.analyze_with_groq(payload, api_key="test-key")
        self.assertEqual(result["status"], "fallback")
        self.assertIn("quota", result["message"].lower())

    @patch("round3_engine.requests.post")
    def test_hypothesis_without_valid_evidence_is_rejected(self, post):
        payload = engine.build_intent_payload(
            "x/y", [{"sha": "abc", "message": "Add login"}], [], []
        )
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"message": {"content": json.dumps({
                "hypotheses": [{"title": "Unsupported", "evidence_refs": ["commit:fake"]}]
            })}}]
        }
        post.return_value = response
        result = engine.analyze_with_groq(payload, api_key="test-key")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["hypotheses"], [])


class HumanReviewTests(unittest.TestCase):
    def test_only_approved_findings_enter_role_report(self):
        findings = [
            {"kind": "AI-generated hypothesis", "title": "Possible auth work",
             "summary": "Evidence suggests auth changes.", "evidence_refs": ["commit:abc"]}
        ]
        reviews = engine.make_review_items(findings)
        review_id = reviews[0]["review_id"]
        reviews = engine.update_review_item(
            reviews, review_id, status="approved",
            edited_title="Authentication workflow", reviewer_note="Checked source commit."
        )
        approved = engine.approved_findings(reviews)
        report = engine.role_handover_report("x/y", "developer", approved)
        self.assertIn("Authentication workflow", report)
        self.assertIn("Checked source commit.", report)


    def test_role_reports_have_distinct_audience_content(self):
        findings = [{"title": "Possible auth work", "kind": "AI-generated hypothesis",
                     "summary": "Authentication changes need verification.",
                     "evidence_refs": ["commit:abc"], "review_status": "Approved by human reviewer"}]
        commits = [{"sha": "abc", "message": "Improve auth", "author": "Dev A",
                    "url": "https://github.com/x/y/commit/abc",
                    "filenames": ["src/auth.py"], "patch_markers": ["TODO"]}]
        issues = [{"number": 7, "title": "Review login edge case", "state": "open",
                   "html_url": "https://github.com/x/y/issues/7"}]
        pulls = [{"number": 8, "title": "Add auth validation", "state": "open",
                  "html_url": "https://github.com/x/y/pull/8"}]
        developer = engine.role_handover_report("x/y", "developer", findings, commits, issues=issues, pulls=pulls)
        maintainer = engine.role_handover_report("x/y", "maintainer", findings, commits, issues=issues, pulls=pulls)
        lead = engine.role_handover_report("x/y", "project_lead", findings, commits, issues=issues, pulls=pulls)
        self.assertIn("Files touched", developer)
        self.assertIn("src/auth.py", developer)
        self.assertIn("Open issues", maintainer)
        self.assertIn("Review login edge case", maintainer)
        self.assertIn("Open pull requests", maintainer)
        self.assertIn("Delivery snapshot", lead)
        self.assertIn("Suggested coordination agenda", lead)
        self.assertNotEqual(developer, maintainer)
        self.assertNotEqual(maintainer, lead)

    def test_maintainer_report_handles_empty_open_queues(self):
        report = engine.role_handover_report("x/y", "maintainer", [], issues=[], pulls=[])
        self.assertIn("No open issues were returned", report)
        self.assertIn("No open pull requests were returned", report)

    def test_invalid_role_is_rejected(self):
        with self.assertRaises(ValueError):
            engine.role_handover_report("x/y", "intern", [])


class NextWorkPathTests(unittest.TestCase):
    def test_open_issue_becomes_high_priority_evidence_linked_task(self):
        issues = [{"number": 12, "title": "Fix login validation", "state": "open",
                   "html_url": "https://github.com/x/y/issues/12"}]
        result = engine.build_next_work_path([], issues, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["priority_label"], "High")
        self.assertEqual(result[0]["evidence"][0]["id"], "issue:12")
        self.assertIn("acceptance criteria", result[0]["suggested_next_steps"][-1])

    def test_open_pull_request_is_ranked_above_model_hypothesis(self):
        pulls = [{"number": 4, "title": "Add validation", "state": "open",
                  "html_url": "https://github.com/x/y/pull/4"}]
        commits = [{"sha": "abc", "message": "Authentication work", "url": "https://github.com/x/y/commit/abc"}]
        hypotheses = [{"title": "Explore auth tests", "summary": "Possible test follow-up.",
                       "suggested_next_steps": ["Inspect tests."], "evidence_refs": ["commit:abc"]}]
        result = engine.build_next_work_path(commits, [], pulls, hypotheses=hypotheses)
        self.assertEqual(result[0]["category"], "Open pull request")
        self.assertEqual(result[1]["category"], "AI-assisted hypothesis")

    def test_commit_follow_up_marker_creates_exploratory_task_with_source(self):
        commits = [{"sha": "abc", "message": "WIP auth flow", "patch_markers": ["TODO"],
                    "filenames": ["src/auth.py"], "url": "https://github.com/x/y/commit/abc"}]
        result = engine.build_next_work_path(commits, [], [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "Possible unfinished work")
        self.assertEqual(result[0]["evidence"][0]["url"], commits[0]["url"])

    def test_related_commit_activity_produces_cautious_exploratory_path(self):
        commits = [
            {"sha": "a", "message": "Add login validation", "filenames": ["src/auth/login.py"],
             "url": "https://github.com/x/y/commit/a"},
            {"sha": "b", "message": "Improve login validation", "filenames": ["src/auth/login.py"],
             "url": "https://github.com/x/y/commit/b"},
        ]
        graph = engine.build_relationship_graph(commits, [], [])
        result = engine.build_next_work_path(commits, [], [], graph=graph)
        self.assertTrue(any(item["category"] == "Related commit activity" for item in result))
        item = next(item for item in result if item["category"] == "Related commit activity")
        self.assertEqual(len(item["evidence"]), 2)
        self.assertIn("does not prove", item["summary"])

    def test_model_hypothesis_without_resolvable_evidence_is_excluded(self):
        hypotheses = [{"title": "Unsupported", "evidence_refs": ["commit:not-real"]}]
        result = engine.build_next_work_path([], [], [], hypotheses=hypotheses)
        self.assertEqual(result, [])

    def test_priority_is_not_described_as_probability(self):
        issues = [{"number": 1, "title": "Bug", "state": "open",
                   "html_url": "https://github.com/x/y/issues/1"}]
        item = engine.build_next_work_path([], issues, [])[0]
        self.assertIn("not a probability", item["disclaimer"])


if __name__ == "__main__":
    unittest.main()
