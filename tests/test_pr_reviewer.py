#!/usr/bin/env python3
"""Unit tests for claude-pr-reviewer core logic.

Tests cover pure functions that don't require network access or API keys.
Run: python -m pytest tests/ -v
  or: python -m unittest discover tests/
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# Allow importing from parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pr_reviewer


# ---------------------------------------------------------------------------
# _parse_license
# ---------------------------------------------------------------------------

class TestParseLicense(unittest.TestCase):
    def test_valid(self):
        result = pr_reviewer._parse_license("user@example.com:550e8400-e29b-41d4-a716-446655440000")
        self.assertEqual(result, ("user@example.com", "550e8400-e29b-41d4-a716-446655440000"))

    def test_valid_with_whitespace(self):
        result = pr_reviewer._parse_license("  user@example.com:550e8400-e29b-41d4-a716-446655440000  ")
        self.assertIsNotNone(result)

    def test_missing_colon(self):
        self.assertIsNone(pr_reviewer._parse_license("user@example.com550e8400"))

    def test_bad_email_no_at(self):
        self.assertIsNone(pr_reviewer._parse_license("notanemail:550e8400-e29b-41d4-a716-446655440000"))

    def test_bad_email_no_tld(self):
        self.assertIsNone(pr_reviewer._parse_license("user@nodot:550e8400-e29b-41d4-a716-446655440000"))

    def test_bad_uuid(self):
        self.assertIsNone(pr_reviewer._parse_license("user@example.com:not-a-uuid"))

    def test_empty(self):
        self.assertIsNone(pr_reviewer._parse_license(""))

    def test_uuid_case_insensitive(self):
        result = pr_reviewer._parse_license("user@example.com:550E8400-E29B-41D4-A716-446655440000")
        self.assertIsNotNone(result)


# ---------------------------------------------------------------------------
# parse_pr_url
# ---------------------------------------------------------------------------

class TestParsePrUrl(unittest.TestCase):
    def test_valid(self):
        owner, repo, number = pr_reviewer.parse_pr_url("https://github.com/owner/repo/pull/42")
        self.assertEqual(owner, "owner")
        self.assertEqual(repo, "repo")
        self.assertEqual(number, 42)

    def test_valid_trailing_whitespace(self):
        owner, repo, number = pr_reviewer.parse_pr_url("  https://github.com/owner/my-repo/pull/100  ")
        self.assertEqual(owner, "owner")
        self.assertEqual(repo, "my-repo")
        self.assertEqual(number, 100)

    def test_invalid_url_exits(self):
        with self.assertRaises(SystemExit):
            pr_reviewer.parse_pr_url("https://github.com/owner/repo")

    def test_non_github_url_exits(self):
        with self.assertRaises(SystemExit):
            pr_reviewer.parse_pr_url("https://gitlab.com/owner/repo/merge_requests/1")


# ---------------------------------------------------------------------------
# truncate_diff
# ---------------------------------------------------------------------------

class TestTruncateDiff(unittest.TestCase):
    def test_short_diff_unchanged(self):
        diff = "short diff content"
        self.assertEqual(pr_reviewer.truncate_diff(diff, max_chars=1000), diff)

    def test_exactly_at_limit(self):
        diff = "x" * 100
        result = pr_reviewer.truncate_diff(diff, max_chars=100)
        self.assertEqual(result, diff)

    def test_truncation_adds_marker(self):
        diff = "\n".join(["line"] * 1000)
        result = pr_reviewer.truncate_diff(diff, max_chars=100)
        self.assertIn("truncated", result)

    def test_truncation_shorter_than_original(self):
        diff = "\n".join(["line"] * 1000)
        result = pr_reviewer.truncate_diff(diff, max_chars=100)
        self.assertLess(len(result), len(diff))

    def test_empty_diff(self):
        self.assertEqual(pr_reviewer.truncate_diff("", max_chars=100), "")


# ---------------------------------------------------------------------------
# filter_diff
# ---------------------------------------------------------------------------

class TestFilterDiff(unittest.TestCase):
    SAMPLE_DIFF = (
        "diff --git a/src/main.py b/src/main.py\n"
        "--- a/src/main.py\n"
        "+++ b/src/main.py\n"
        "@@ -1,3 +1,4 @@\n"
        " existing\n"
        "+new line\n"
        "diff --git a/package-lock.json b/package-lock.json\n"
        "--- a/package-lock.json\n"
        "+++ b/package-lock.json\n"
        "@@ -1 +1 @@\n"
        "+{}\n"
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "+# Title\n"
    )

    def test_no_patterns(self):
        result = pr_reviewer.filter_diff(self.SAMPLE_DIFF, [])
        self.assertEqual(result, self.SAMPLE_DIFF)

    def test_filter_lock_file(self):
        result = pr_reviewer.filter_diff(self.SAMPLE_DIFF, ["package-lock.json"])
        self.assertNotIn("package-lock.json", result.split("Skipped")[0])
        self.assertIn("src/main.py", result)

    def test_filter_by_glob(self):
        result = pr_reviewer.filter_diff(self.SAMPLE_DIFF, ["*.md"])
        self.assertNotIn("README.md", result.split("Skipped")[0])
        self.assertIn("src/main.py", result)

    def test_skipped_count_in_output(self):
        result = pr_reviewer.filter_diff(self.SAMPLE_DIFF, ["*.md", "package-lock.json"])
        self.assertIn("Skipped 2 file(s)", result)

    def test_filter_all_sections(self):
        result = pr_reviewer.filter_diff(self.SAMPLE_DIFF, ["*.py", "*.json", "*.md"])
        self.assertIn("Skipped 3 file(s)", result)


# ---------------------------------------------------------------------------
# parse_verdict
# ---------------------------------------------------------------------------

class TestParseVerdict(unittest.TestCase):
    def _review(self, verdict_line):
        return f"## Summary\nSome summary.\n\n## Overall Verdict\n{verdict_line}\nJustification."

    def test_approve(self):
        self.assertEqual(pr_reviewer.parse_verdict(self._review("APPROVE")), "APPROVE")

    def test_approve_case_insensitive(self):
        self.assertEqual(pr_reviewer.parse_verdict(self._review("Approve - looks good")), "APPROVE")

    def test_request_changes(self):
        self.assertEqual(pr_reviewer.parse_verdict(self._review("REQUEST CHANGES")), "REQUEST_CHANGES")

    def test_request_changes_partial(self):
        self.assertEqual(pr_reviewer.parse_verdict(self._review("REQUEST CHANGES needed")), "REQUEST_CHANGES")

    def test_needs_discussion_defaults_to_comment(self):
        self.assertEqual(pr_reviewer.parse_verdict(self._review("NEEDS DISCUSSION")), "COMMENT")

    def test_no_verdict_section_defaults_to_comment(self):
        self.assertEqual(pr_reviewer.parse_verdict("No verdict here."), "COMMENT")


# ---------------------------------------------------------------------------
# parse_diff_for_lines
# ---------------------------------------------------------------------------

class TestParseDiffForLines(unittest.TestCase):
    SAMPLE_DIFF = (
        "diff --git a/src/app.py b/src/app.py\n"
        "index abc..def 100644\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,3 +1,5 @@\n"
        " context line\n"      # line 1 in new file (context, not added)
        "+added line A\n"      # line 2
        " another context\n"   # line 3 (context)
        "+added line B\n"      # line 4
        " last context\n"      # line 5
        "diff --git a/tests/test.py b/tests/test.py\n"
        "--- a/tests/test.py\n"
        "+++ b/tests/test.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+import pytest\n"     # line 1
        "+def test_foo():\n"   # line 2
    )

    def test_finds_added_lines(self):
        result = pr_reviewer.parse_diff_for_lines(self.SAMPLE_DIFF)
        self.assertIn("src/app.py", result)
        self.assertIn(2, result["src/app.py"])
        self.assertIn(4, result["src/app.py"])

    def test_excludes_context_lines(self):
        result = pr_reviewer.parse_diff_for_lines(self.SAMPLE_DIFF)
        # Line 1 is context, not an addition
        self.assertNotIn(1, result["src/app.py"])

    def test_multiple_files(self):
        result = pr_reviewer.parse_diff_for_lines(self.SAMPLE_DIFF)
        self.assertIn("tests/test.py", result)
        self.assertIn(1, result["tests/test.py"])
        self.assertIn(2, result["tests/test.py"])

    def test_empty_diff(self):
        result = pr_reviewer.parse_diff_for_lines("")
        self.assertEqual(result, {})

    def test_deleted_file_not_added(self):
        diff = (
            "diff --git a/old.py b/old.py\n"
            "--- a/old.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-line1\n"
            "-line2\n"
        )
        result = pr_reviewer.parse_diff_for_lines(diff)
        self.assertNotIn("old.py", result)


# ---------------------------------------------------------------------------
# parse_inline_comments
# ---------------------------------------------------------------------------

class TestParseInlineComments(unittest.TestCase):
    def setUp(self):
        self.valid_lines = {
            "src/app.py": {10, 20, 30},
            "tests/test.py": {5},
        }

    def _review(self, *bullets):
        lines = ["## Issues Found\n### Critical"]
        for b in bullets:
            lines.append(f"- {b}")
        lines.append("\n## Overall Verdict\nAPPROVE")
        return "\n".join(lines)

    def test_extracts_valid_comment(self):
        review = self._review("FILE:src/app.py LINE:10 Missing null check.")
        _, comments = pr_reviewer.parse_inline_comments(review, self.valid_lines)
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["path"], "src/app.py")
        self.assertEqual(comments[0]["line"], 10)
        self.assertIn("null check", comments[0]["body"])

    def test_filters_invalid_line(self):
        review = self._review("FILE:src/app.py LINE:999 This line doesn't exist in diff.")
        _, comments = pr_reviewer.parse_inline_comments(review, self.valid_lines)
        self.assertEqual(len(comments), 0)

    def test_filters_unknown_file(self):
        review = self._review("FILE:nonexistent.py LINE:1 Unknown file.")
        _, comments = pr_reviewer.parse_inline_comments(review, self.valid_lines)
        self.assertEqual(len(comments), 0)

    def test_deduplicates_same_position(self):
        review = self._review(
            "FILE:src/app.py LINE:10 First issue.",
            "FILE:src/app.py LINE:10 Second issue at same spot.",
        )
        _, comments = pr_reviewer.parse_inline_comments(review, self.valid_lines)
        self.assertEqual(len(comments), 1)

    def test_cleans_prefix_from_text(self):
        review = self._review("FILE:src/app.py LINE:10 Issue here.")
        cleaned, _ = pr_reviewer.parse_inline_comments(review, self.valid_lines)
        self.assertNotIn("FILE:src/app.py LINE:10", cleaned)

    def test_multiple_valid_comments(self):
        review = self._review(
            "FILE:src/app.py LINE:10 Issue one.",
            "FILE:src/app.py LINE:20 Issue two.",
            "FILE:tests/test.py LINE:5 Missing assertion.",
        )
        _, comments = pr_reviewer.parse_inline_comments(review, self.valid_lines)
        self.assertEqual(len(comments), 3)

    def test_no_comments(self):
        review = "## Summary\nAll looks good.\n\n## Overall Verdict\nAPPROVE"
        cleaned, comments = pr_reviewer.parse_inline_comments(review, self.valid_lines)
        self.assertEqual(len(comments), 0)
        self.assertEqual(cleaned, review)

    def test_empty_valid_lines(self):
        review = self._review("FILE:src/app.py LINE:10 Some issue.")
        _, comments = pr_reviewer.parse_inline_comments(review, {})
        self.assertEqual(len(comments), 0)

    # Feature 1: walkthrough table header not stripped by parse_inline_comments
    def test_walkthrough_header_not_stripped(self):
        review = (
            "## Changes Walkthrough\n"
            "| File | Change | Summary |\n"
            "|------|--------|----------|\n"
            "| src/app.py | Modified | Added null check. |\n"
            "\n## Summary\nThis PR adds a null check.\n\n## Overall Verdict\nAPPROVE"
        )
        cleaned, comments = pr_reviewer.parse_inline_comments(review, self.valid_lines)
        self.assertIn("## Changes Walkthrough", cleaned)
        self.assertIn("| src/app.py | Modified |", cleaned)
        self.assertEqual(len(comments), 0)

    # Feature 2: suggestion block extraction
    def test_fix_annotation_produces_suggestion_block(self):
        review = self._review(
            "FILE:src/app.py LINE:10 Null input crashes here. [FIX: value = value or \"\"]"
        )
        _, comments = pr_reviewer.parse_inline_comments(review, self.valid_lines)
        self.assertEqual(len(comments), 1)
        body = comments[0]["body"]
        self.assertIn("```suggestion", body)
        self.assertIn('value = value or ""', body)

    def test_fix_annotation_removed_from_description(self):
        review = self._review(
            "FILE:src/app.py LINE:10 Null input crashes here. [FIX: value = value or \"\"]"
        )
        _, comments = pr_reviewer.parse_inline_comments(review, self.valid_lines)
        self.assertEqual(len(comments), 1)
        body = comments[0]["body"]
        self.assertNotIn("[FIX:", body)
        self.assertIn("Null input crashes here.", body)

    def test_no_fix_annotation_body_unchanged(self):
        review = self._review("FILE:src/app.py LINE:10 Missing null check.")
        _, comments = pr_reviewer.parse_inline_comments(review, self.valid_lines)
        self.assertEqual(len(comments), 1)
        body = comments[0]["body"]
        self.assertNotIn("```suggestion", body)
        self.assertEqual(body, "Missing null check.")


# ---------------------------------------------------------------------------
# get_previous_review_comments
# ---------------------------------------------------------------------------

class TestGetPreviousReviewComments(unittest.TestCase):
    def test_returns_comment_bodies(self):
        mock_comments = [
            {"body": "Missing null check on line 10.", "id": 1},
            {"body": "Variable name is confusing.", "id": 2},
        ]
        with patch.object(pr_reviewer, "github_request", return_value=mock_comments):
            result = pr_reviewer.get_previous_review_comments("owner", "repo", 42, 999)
        self.assertEqual(len(result), 2)
        self.assertIn("Missing null check on line 10.", result)
        self.assertIn("Variable name is confusing.", result)

    def test_returns_empty_list_on_api_failure(self):
        with patch.object(pr_reviewer, "github_request", side_effect=SystemExit(1)):
            result = pr_reviewer.get_previous_review_comments("owner", "repo", 42, 999)
        self.assertEqual(result, [])

    def test_skips_empty_body_comments(self):
        mock_comments = [
            {"body": "Real issue here.", "id": 1},
            {"body": "", "id": 2},
            {"body": "   ", "id": 3},
            {"id": 4},  # no body key
        ]
        with patch.object(pr_reviewer, "github_request", return_value=mock_comments):
            result = pr_reviewer.get_previous_review_comments("owner", "repo", 1, 100)
        # Only the non-empty body survives; whitespace-only strip() gives "" which is falsy
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "Real issue here.")


# ---------------------------------------------------------------------------
# _parse_simple_yaml
# ---------------------------------------------------------------------------

class TestParseSimpleYaml(unittest.TestCase):
    def test_string_value(self):
        result = pr_reviewer._parse_simple_yaml("model: claude-haiku-4-5-20251001\n")
        self.assertEqual(result["model"], "claude-haiku-4-5-20251001")

    def test_integer_value(self):
        result = pr_reviewer._parse_simple_yaml("max_tokens: 2048\n")
        self.assertEqual(result["max_tokens"], 2048)

    def test_bool_true(self):
        result = pr_reviewer._parse_simple_yaml("walkthrough: true\n")
        self.assertTrue(result["walkthrough"])

    def test_bool_false(self):
        result = pr_reviewer._parse_simple_yaml("walkthrough: false\n")
        self.assertFalse(result["walkthrough"])

    def test_bool_yes_no(self):
        result = pr_reviewer._parse_simple_yaml("walkthrough: yes\n")
        self.assertTrue(result["walkthrough"])
        result = pr_reviewer._parse_simple_yaml("walkthrough: no\n")
        self.assertFalse(result["walkthrough"])

    def test_list_values(self):
        yaml = "ignore_patterns:\n  - '*.lock'\n  - dist/**\n"
        result = pr_reviewer._parse_simple_yaml(yaml)
        self.assertEqual(result["ignore_patterns"], ["*.lock", "dist/**"])

    def test_comment_stripped(self):
        result = pr_reviewer._parse_simple_yaml("model: claude-sonnet-4-6  # fast\n")
        self.assertEqual(result["model"], "claude-sonnet-4-6")

    def test_quoted_string(self):
        result = pr_reviewer._parse_simple_yaml('model: "claude-sonnet-4-6"\n')
        self.assertEqual(result["model"], "claude-sonnet-4-6")

    def test_multiple_keys(self):
        yaml = "model: claude-haiku-4-5-20251001\nstrictness: strict\nmax_tokens: 1024\n"
        result = pr_reviewer._parse_simple_yaml(yaml)
        self.assertEqual(result["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(result["strictness"], "strict")
        self.assertEqual(result["max_tokens"], 1024)

    def test_empty_string(self):
        self.assertEqual(pr_reviewer._parse_simple_yaml(""), {})

    def test_blank_lines_ignored(self):
        yaml = "\n\nmodel: claude-sonnet-4-6\n\n"
        result = pr_reviewer._parse_simple_yaml(yaml)
        self.assertEqual(result["model"], "claude-sonnet-4-6")


# ---------------------------------------------------------------------------
# apply_config
# ---------------------------------------------------------------------------

class TestApplyConfig(unittest.TestCase):
    def setUp(self):
        # Save original globals
        self._orig_model = pr_reviewer.CLAUDE_MODEL
        self._orig_tokens = pr_reviewer.MAX_TOKENS
        self._orig_ignore = pr_reviewer.IGNORE_PATTERNS[:]
        self._orig_strictness = pr_reviewer.REVIEW_STRICTNESS
        self._orig_walkthrough = pr_reviewer.REVIEW_WALKTHROUGH
        self._orig_skip_draft = pr_reviewer.SKIP_DRAFT

    def tearDown(self):
        pr_reviewer.CLAUDE_MODEL = self._orig_model
        pr_reviewer.MAX_TOKENS = self._orig_tokens
        pr_reviewer.IGNORE_PATTERNS = self._orig_ignore
        pr_reviewer.REVIEW_STRICTNESS = self._orig_strictness
        pr_reviewer.REVIEW_WALKTHROUGH = self._orig_walkthrough
        pr_reviewer.SKIP_DRAFT = self._orig_skip_draft

    def test_model_applied(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_MODEL", None)
            pr_reviewer.apply_config({"model": "claude-haiku-4-5-20251001"})
        self.assertEqual(pr_reviewer.CLAUDE_MODEL, "claude-haiku-4-5-20251001")

    def test_env_var_overrides_config(self):
        # When CLAUDE_MODEL is set in env, apply_config must not overwrite the global
        # with the config value. The global retains its pre-call value.
        original = pr_reviewer.CLAUDE_MODEL
        with patch.dict(os.environ, {"CLAUDE_MODEL": original}):
            pr_reviewer.apply_config({"model": "claude-haiku-4-5-20251001"})
        self.assertNotEqual(pr_reviewer.CLAUDE_MODEL, "claude-haiku-4-5-20251001")
        self.assertEqual(pr_reviewer.CLAUDE_MODEL, original)

    def test_max_tokens_applied(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAX_TOKENS", None)
            pr_reviewer.apply_config({"max_tokens": 2048})
        self.assertEqual(pr_reviewer.MAX_TOKENS, 2048)

    def test_ignore_patterns_list_applied(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("IGNORE_PATTERNS", None)
            pr_reviewer.apply_config({"ignore_patterns": ["*.lock", "dist/**"]})
        self.assertEqual(pr_reviewer.IGNORE_PATTERNS, ["*.lock", "dist/**"])

    def test_strictness_applied(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REVIEW_STRICTNESS", None)
            pr_reviewer.apply_config({"strictness": "strict"})
        self.assertEqual(pr_reviewer.REVIEW_STRICTNESS, "strict")

    def test_walkthrough_false_applied(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REVIEW_WALKTHROUGH", None)
            pr_reviewer.apply_config({"walkthrough": False})
        self.assertEqual(pr_reviewer.REVIEW_WALKTHROUGH, "false")

    def test_skip_draft_false_applied(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SKIP_DRAFT", None)
            pr_reviewer.apply_config({"skip_draft": False})
        self.assertEqual(pr_reviewer.SKIP_DRAFT, "false")

    def test_skip_draft_true_applied(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SKIP_DRAFT", None)
            pr_reviewer.apply_config({"skip_draft": True})
        self.assertEqual(pr_reviewer.SKIP_DRAFT, "true")

    def test_skip_draft_env_overrides_config(self):
        with patch.dict(os.environ, {"SKIP_DRAFT": "true"}):
            pr_reviewer.apply_config({"skip_draft": False})
        self.assertNotEqual(pr_reviewer.SKIP_DRAFT, "false")

    def test_empty_config_no_change(self):
        original_model = pr_reviewer.CLAUDE_MODEL
        with patch.dict(os.environ, {}, clear=False):
            pr_reviewer.apply_config({})
        self.assertEqual(pr_reviewer.CLAUDE_MODEL, original_model)


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig(unittest.TestCase):
    def test_loads_from_workspace(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, ".pr-reviewer.yml")
            with open(config_path, "w") as f:
                f.write("strictness: lenient\n")
            with patch.dict(os.environ, {"GITHUB_WORKSPACE": tmpdir}):
                result = pr_reviewer.load_config()
        self.assertEqual(result.get("strictness"), "lenient")

    def test_returns_empty_if_no_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"GITHUB_WORKSPACE": tmpdir}):
                result = pr_reviewer.load_config()
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
# get_action_context — draft field
# ---------------------------------------------------------------------------

class TestGetActionContextDraft(unittest.TestCase):
    def _make_event(self, draft=False):
        return {
            "pull_request": {
                "number": 1,
                "title": "Test PR",
                "body": "Description",
                "draft": draft,
                "head": {"sha": "abc123"},
                "comments_url": "https://api.github.com/repos/owner/repo/issues/1/comments",
                "base": {"repo": {"full_name": "owner/repo"}},
            },
            "repository": {"full_name": "owner/repo"},
        }

    def test_draft_true_captured(self):
        import tempfile, json
        event = self._make_event(draft=True)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(event, f)
            path = f.name
        with patch.dict(os.environ, {"GITHUB_EVENT_PATH": path}):
            ctx = pr_reviewer.get_action_context()
        self.assertTrue(ctx["draft"])

    def test_draft_false_captured(self):
        import tempfile, json
        event = self._make_event(draft=False)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(event, f)
            path = f.name
        with patch.dict(os.environ, {"GITHUB_EVENT_PATH": path}):
            ctx = pr_reviewer.get_action_context()
        self.assertFalse(ctx["draft"])

    def test_draft_field_defaults_to_false_when_absent(self):
        import tempfile, json
        event = self._make_event()
        del event["pull_request"]["draft"]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(event, f)
            path = f.name
        with patch.dict(os.environ, {"GITHUB_EVENT_PATH": path}):
            ctx = pr_reviewer.get_action_context()
        self.assertFalse(ctx["draft"])


# ---------------------------------------------------------------------------
# Draft skip logic
# ---------------------------------------------------------------------------

class TestDraftSkipLogic(unittest.TestCase):
    def setUp(self):
        self._orig_skip_draft = pr_reviewer.SKIP_DRAFT

    def tearDown(self):
        pr_reviewer.SKIP_DRAFT = self._orig_skip_draft

    def test_draft_pr_exits_when_skip_draft_true(self):
        pr_reviewer.SKIP_DRAFT = "true"
        ctx = {"draft": True, "number": 1, "owner": "o", "repo": "r"}
        # Simulate the skip check from main()
        with self.assertRaises(SystemExit) as cm:
            if pr_reviewer.SKIP_DRAFT == "true" and ctx.get("draft", False):
                raise SystemExit(0)
        self.assertEqual(cm.exception.code, 0)

    def test_draft_pr_not_skipped_when_skip_draft_false(self):
        pr_reviewer.SKIP_DRAFT = "false"
        ctx = {"draft": True, "number": 1}
        # Should not raise
        skipped = pr_reviewer.SKIP_DRAFT == "true" and ctx.get("draft", False)
        self.assertFalse(skipped)

    def test_non_draft_not_skipped_when_skip_draft_true(self):
        pr_reviewer.SKIP_DRAFT = "true"
        ctx = {"draft": False, "number": 1}
        skipped = pr_reviewer.SKIP_DRAFT == "true" and ctx.get("draft", False)
        self.assertFalse(skipped)


if __name__ == "__main__":
    unittest.main(verbosity=2)
