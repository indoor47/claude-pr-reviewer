#!/usr/bin/env python3
"""
claude-pr-reviewer: Review any GitHub PR with Claude AI

CLI:    python pr_reviewer.py <github-pr-url>
Action: Runs automatically on pull_request events (see action.yml)

Requires:
  ANTHROPIC_API_KEY  - your Anthropic API key
  GITHUB_TOKEN       - optional for CLI, automatic in Actions
"""

import os
import sys
import re
import json
import urllib.request
import urllib.error


ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))

REVIEW_PROMPT = """You are a senior software engineer doing a thorough code review.

Review the following pull request diff and provide structured feedback.

PR Title: {title}
PR Description: {body}

Diff:
{diff}

Respond in this exact format:

## Summary
One paragraph describing what this PR does.

## Issues Found

### Critical (must fix before merge)
- List each critical bug, security issue, or correctness problem. Be specific with line references.
- If none: "None found."

### Major (should fix)
- Logic problems, performance issues, poor error handling, missing validation.
- If none: "None found."

### Minor (consider fixing)
- Style, naming, missing comments on complex logic, small improvements.
- If none: "None found."

## Security
Any security concerns (injection, auth bypasses, exposed secrets, etc).
If none: "No security concerns identified."

## Overall Verdict
APPROVE / REQUEST CHANGES / NEEDS DISCUSSION
One sentence justification.
"""


def github_request(url):
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "claude-pr-reviewer"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"GitHub API error {e.code}: {body}", file=sys.stderr)
        sys.exit(1)


def github_diff_request(url):
    headers = {
        "Accept": "application/vnd.github.v3.diff",
        "User-Agent": "claude-pr-reviewer",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        print(f"GitHub diff error {e.code}", file=sys.stderr)
        sys.exit(1)


def github_post(url, data):
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "claude-pr-reviewer",
        "Content-Type": "application/json",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"GitHub POST error {e.code}: {body}", file=sys.stderr)
        sys.exit(1)


def claude_review(prompt):
    if not ANTHROPIC_API_KEY:
        print("Error: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            return data["content"][0]["text"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Anthropic API error {e.code}: {body}", file=sys.stderr)
        sys.exit(1)


def parse_pr_url(url):
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url.strip())
    if not match:
        print("Error: expected URL like https://github.com/owner/repo/pull/123", file=sys.stderr)
        sys.exit(1)
    return match.group(1), match.group(2), int(match.group(3))


def truncate_diff(diff, max_chars=40000):
    if len(diff) <= max_chars:
        return diff
    lines = diff.splitlines()
    kept = []
    total = 0
    for line in lines:
        total += len(line) + 1
        if total > max_chars:
            kept.append(f"\n... diff truncated at {max_chars} chars ...")
            break
        kept.append(line)
    return "\n".join(kept)


def get_action_context():
    """Read PR details from GitHub Actions event payload."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    try:
        with open(event_path) as f:
            event = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    pr = event.get("pull_request")
    if not pr:
        print("No pull_request in event payload. Is this a pull_request trigger?", file=sys.stderr)
        sys.exit(1)

    repo = event.get("repository", {}).get("full_name", "")
    if not repo:
        repo = pr.get("base", {}).get("repo", {}).get("full_name", "")

    owner, repo_name = repo.split("/", 1) if "/" in repo else ("", "")
    return {
        "owner": owner,
        "repo": repo_name,
        "number": pr["number"],
        "title": pr.get("title", ""),
        "body": pr.get("body") or "(no description)",
        "comments_url": pr.get("comments_url") or f"https://api.github.com/repos/{repo}/issues/{pr['number']}/comments",
    }


def run_review(owner, repo, pr_number):
    """Fetch PR data, get diff, run Claude review, return the review text."""
    api_base = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"

    pr_data = github_request(api_base)
    title = pr_data.get("title", "")
    body = pr_data.get("body") or "(no description)"
    additions = pr_data.get("additions", 0)
    deletions = pr_data.get("deletions", 0)
    changed_files = pr_data.get("changed_files", 0)

    print(f"  PR #{pr_number}: {title}")
    print(f"  Changes: +{additions} -{deletions} across {changed_files} files")

    diff = github_diff_request(api_base)
    diff = truncate_diff(diff)

    prompt = REVIEW_PROMPT.format(title=title, body=body, diff=diff)

    print(f"  Reviewing with {CLAUDE_MODEL}...")
    review = claude_review(prompt)
    return review, title


def main():
    is_action = os.environ.get("GITHUB_ACTIONS") == "true"

    if is_action:
        ctx = get_action_context()
        if not ctx:
            print("Could not read GitHub Actions event context.", file=sys.stderr)
            sys.exit(1)

        review, title = run_review(ctx["owner"], ctx["repo"], ctx["number"])

        comment_body = f"## Claude Code Review\n\n{review}\n\n---\n*Reviewed by [claude-pr-reviewer](https://github.com/indoor47/claude-pr-reviewer) using {CLAUDE_MODEL}*"

        print("  Posting review comment...")
        github_post(ctx["comments_url"], {"body": comment_body})
        print(f"  Review posted on PR #{ctx['number']}")

    else:
        if len(sys.argv) < 2:
            print("Usage: python pr_reviewer.py <github-pr-url>")
            print("       python pr_reviewer.py https://github.com/owner/repo/pull/123")
            sys.exit(1)

        pr_url = sys.argv[1]
        owner, repo, pr_number = parse_pr_url(pr_url)

        print(f"Fetching PR #{pr_number} from {owner}/{repo}...")
        review, title = run_review(owner, repo, pr_number)

        print("\n" + "=" * 60)
        print(review)
        print("=" * 60)
        print(f"\nReviewed: {pr_url}")


if __name__ == "__main__":
    main()
