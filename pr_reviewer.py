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
import fnmatch
import urllib.request
import urllib.error
import pathlib


ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
HOSTED_API_KEY = os.environ.get("HOSTED_API_KEY")
HOSTED_API_URL = os.environ.get("HOSTED_API_URL", "https://api.memfun.dev")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))
IGNORE_PATTERNS = [p.strip() for p in os.environ.get("IGNORE_PATTERNS", "").split(",") if p.strip()]
REVIEW_STRICTNESS = os.environ.get("REVIEW_STRICTNESS", "balanced")

MODEL_PRICING_PER_MTOK = {
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
}

LICENSE_DIR = pathlib.Path.home() / ".claude-pr-reviewer"
LICENSE_FILE = LICENSE_DIR / "license"

STRIPE_URL = "STRIPE_URL_PLACEHOLDER"
SUPPORT_EMAIL = "adamai@agentmail.to"
REVIEW_MARKER = "<!-- claude-pr-reviewer -->"

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_STRICTNESS_ADDENDUM = {
    "lenient": (
        "IMPORTANT: Focus ONLY on Critical bugs and Security issues. "
        "For the Major and Minor sections, write 'Skipped (lenient mode).' "
        "Do not comment on style, naming, or minor improvements."
    ),
    "balanced": "",
    "strict": (
        "IMPORTANT: Be thorough. Beyond standard review categories, also flag: "
        "missing test coverage for new public functions, undocumented public APIs, "
        "performance bottlenecks, and technical debt being introduced."
    ),
}


def format_cost_estimate(usage, model):
    """Return a cost estimate string from Anthropic usage data."""
    input_t = usage.get("input_tokens", 0)
    output_t = usage.get("output_tokens", 0)
    if not input_t:
        return ""
    prices = MODEL_PRICING_PER_MTOK.get(model, (3.0, 15.0))
    cost = (input_t * prices[0] + output_t * prices[1]) / 1_000_000
    return f"  Tokens: {input_t:,} in / {output_t:,} out | Est. cost: ~${cost:.4f}"


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
- List each critical bug, security issue, or correctness problem.
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

When an issue can be pinpointed to a specific line in the diff, prefix that bullet with FILE:path/to/file LINE:N using the exact file path from the diff header and the line number in the new file. Only use this prefix when you are certain of the exact line. Example:
- FILE:src/auth.py LINE:23 Missing input validation allows injection.
"""


# ---------------------------------------------------------------------------
# License helpers
# ---------------------------------------------------------------------------

def _parse_license(raw):
    """Return (email, uuid) or None if the format is invalid."""
    raw = raw.strip()
    parts = raw.split(":", 1)
    if len(parts) != 2:
        return None
    email, uuid = parts[0].strip(), parts[1].strip()
    # basic email sanity
    if "@" not in email or "." not in email.split("@")[-1]:
        return None
    if not UUID_RE.match(uuid):
        return None
    return email, uuid


def load_license():
    """
    Read ~/.claude-pr-reviewer/license.
    Returns (email, uuid) on success, None if missing or malformed.
    """
    if not LICENSE_FILE.exists():
        return None
    try:
        raw = LICENSE_FILE.read_text(encoding="utf-8")
    except OSError:
        return None
    return _parse_license(raw)


def activate_paid_tier():
    """
    Called when --paid is supplied via CLI.

    - License found and valid  -> set model to opus, print confirmation, return True
    - License found but bad    -> print error, exit 1
    - License missing          -> print payment instructions, exit 0
    """
    result = load_license()

    if LICENSE_FILE.exists() and result is None:
        # File exists but content is malformed
        print(
            "Error: license file at ~/.claude-pr-reviewer/license is malformed.\n"
            "Expected format: your@email.com:550e8400-e29b-41d4-a716-446655440000\n"
            "Re-purchase or contact " + SUPPORT_EMAIL + " for a replacement key.",
            file=sys.stderr,
        )
        sys.exit(1)

    if result is None:
        # No license at all
        _print_payment_instructions()
        sys.exit(0)

    email, uuid = result
    # Activate opus
    global CLAUDE_MODEL
    CLAUDE_MODEL = "claude-opus-4-6"
    print(f"Paid tier active ({email}) — model set to claude-opus-4-6")
    return True


def _print_payment_instructions():
    sep = "=" * 60
    print(f"\n{sep}")
    print("Paid Tier")
    print(sep)
    print()
    print("Unlock the paid tier to use Claude Opus (higher accuracy)")
    print("and future premium features.")
    print()
    print("1. Purchase access:")
    print(f"   {STRIPE_URL}")
    print()
    print("2. Email your receipt to:")
    print(f"   {SUPPORT_EMAIL}")
    print("   Subject: PR Reviewer License")
    print()
    print("3. You will receive a license key within 24 hours.")
    print("   Save it to:")
    print("   ~/.claude-pr-reviewer/license")
    print()
    print("   Format:  your@email.com:550e8400-e29b-41d4-a716-446655440000")
    print()
    print(f"{sep}\n")


# ---------------------------------------------------------------------------
# GitHub / Anthropic helpers
# ---------------------------------------------------------------------------

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


def github_patch(url, data):
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "claude-pr-reviewer",
        "Content-Type": "application/json",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, headers=headers, method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"GitHub PATCH error {e.code}: {body}", file=sys.stderr)
        sys.exit(1)


def find_existing_review_comment(comments_url):
    """Return the comment ID of an existing review comment, or None."""
    page = 1
    while True:
        url = f"{comments_url}?per_page=100&page={page}"
        comments = github_request(url)
        if not comments:
            return None
        for comment in comments:
            if REVIEW_MARKER in comment.get("body", ""):
                return comment["id"]
        if len(comments) < 100:
            return None
        page += 1


def parse_diff_for_lines(diff):
    """Parse unified diff to map file paths to valid new-file line numbers.

    Returns dict: {file_path: set(line_numbers)} for all added/context lines.
    Only addition lines ('+') are valid targets for inline review comments.
    """
    result = {}
    current_file = None
    new_line_num = 0

    for line in diff.split('\n'):
        if line.startswith('+++ b/'):
            current_file = line[6:].strip()
            result.setdefault(current_file, set())
            new_line_num = 0
        elif line.startswith('+++ /dev/null'):
            current_file = None
        elif line.startswith('@@ ') and current_file:
            m = re.search(r'\+(\d+)(?:,\d+)?', line)
            if m:
                new_line_num = int(m.group(1)) - 1
        elif current_file:
            if line.startswith('+') and not line.startswith('+++'):
                new_line_num += 1
                result[current_file].add(new_line_num)
            elif not line.startswith('-'):
                new_line_num += 1

    return result


def parse_verdict(review_text):
    """Extract GitHub PR review event type from the Overall Verdict section."""
    m = re.search(r'## Overall Verdict\s*\n([^\n]+)', review_text, re.IGNORECASE)
    if m:
        verdict = m.group(1).upper()
        if 'APPROVE' in verdict:
            return 'APPROVE'
        if 'REQUEST' in verdict:
            return 'REQUEST_CHANGES'
    return 'COMMENT'


def parse_inline_comments(review_text, valid_lines):
    """Extract FILE:path LINE:N prefixed issues from review text.

    Returns (cleaned_text, [{"path": ..., "line": ..., "body": ...}]).
    Only includes comments whose (path, line) exist in valid_lines.
    """
    pattern = re.compile(r'FILE:(\S+)\s+LINE:(\d+)\s+(.+)')
    comments = []
    used_positions = set()

    for m in pattern.finditer(review_text):
        path = m.group(1).rstrip('.,')
        line = int(m.group(2))
        body = m.group(3).strip()
        pos_key = (path, line)

        if path not in valid_lines or line not in valid_lines[path]:
            continue
        if pos_key in used_positions:
            continue
        if body:
            comments.append({"path": path, "line": line, "body": body})
            used_positions.add(pos_key)

    cleaned = re.sub(r'FILE:\S+\s+LINE:\d+\s+', '', review_text)
    return cleaned, comments


def find_existing_pr_review(owner, repo, pr_number):
    """Find an existing PR review with our marker. Returns review ID or None."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews?per_page=100"
    try:
        reviews = github_request(url)
        for review in reviews:
            if REVIEW_MARKER in review.get("body", ""):
                return review["id"]
    except SystemExit:
        pass
    return None


def post_pr_review(owner, repo, pr_number, head_sha, body, event, comments):
    """Submit a formal PR review with optional inline comments."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    payload = {"commit_id": head_sha, "body": body, "event": event}
    if comments:
        payload["comments"] = comments
    return github_post(url, payload)


def hosted_review(pr_url):
    """Call the hosted review API. Returns review text."""
    payload = json.dumps({"pr_url": pr_url, "api_key": HOSTED_API_KEY}).encode()
    req = urllib.request.Request(
        f"{HOSTED_API_URL}/review",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "claude-pr-reviewer"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            return data.get("review", "")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Hosted API error {e.code}: {body}", file=sys.stderr)
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
            usage = data.get("usage", {})
            return data["content"][0]["text"], usage
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


def filter_diff(diff, ignore_patterns):
    """Remove diff sections for files matching any of the ignore patterns."""
    if not ignore_patterns:
        return diff

    sections = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    kept = []
    skipped = []

    for section in sections:
        if not section.startswith("diff --git"):
            kept.append(section)
            continue

        first_line = section.split("\n")[0]
        match = re.match(r"^diff --git a/(.+) b/(.+)$", first_line)
        if not match:
            kept.append(section)
            continue

        filename = match.group(2)
        if any(fnmatch.fnmatch(filename, pat) for pat in ignore_patterns):
            skipped.append(filename)
            continue

        kept.append(section)

    result = "".join(kept)
    if skipped:
        result += f"\n\n[Skipped {len(skipped)} file(s) matching ignore patterns: {', '.join(skipped[:5])}{'...' if len(skipped) > 5 else ''}]"
    return result


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
        "head_sha": pr.get("head", {}).get("sha", ""),
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
    diff = filter_diff(diff, IGNORE_PATTERNS)
    diff = truncate_diff(diff)

    prompt = REVIEW_PROMPT.format(title=title, body=body, diff=diff)
    strictness_note = _STRICTNESS_ADDENDUM.get(REVIEW_STRICTNESS, "")
    if strictness_note:
        prompt += f"\n\n{strictness_note}"

    print(f"  Reviewing with {CLAUDE_MODEL} (strictness: {REVIEW_STRICTNESS})...")
    review, usage = claude_review(prompt)
    cost_line = format_cost_estimate(usage, CLAUDE_MODEL)
    if cost_line:
        print(cost_line)
    return review, title, diff


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    is_action = os.environ.get("GITHUB_ACTIONS") == "true"
    use_paid = "--paid" in sys.argv

    if use_paid:
        sys.argv.remove("--paid")
        if not is_action:
            activate_paid_tier()   # sets CLAUDE_MODEL to opus or exits

    if is_action:
        ctx = get_action_context()
        if not ctx:
            print("Could not read GitHub Actions event context.", file=sys.stderr)
            sys.exit(1)

        pr_url = f"https://github.com/{ctx['owner']}/{ctx['repo']}/pull/{ctx['number']}"

        if HOSTED_API_KEY:
            print(f"  Using hosted review API for PR #{ctx['number']}...")
            review = hosted_review(pr_url)
            footer = f"\n\n---\n*Reviewed by [claude-pr-reviewer](https://github.com/indoor47/claude-pr-reviewer) (hosted tier)*"
            comment_body = f"{REVIEW_MARKER}\n## Claude Code Review\n\n{review}{footer}"
            existing_id = find_existing_review_comment(ctx["comments_url"])
            if existing_id:
                print("  Updating existing review comment...")
                repo_path = f"{ctx['owner']}/{ctx['repo']}"
                patch_url = f"https://api.github.com/repos/{repo_path}/issues/comments/{existing_id}"
                github_patch(patch_url, {"body": comment_body})
                print(f"  Review updated on PR #{ctx['number']}")
            else:
                print("  Posting review comment...")
                github_post(ctx["comments_url"], {"body": comment_body})
                print(f"  Review posted on PR #{ctx['number']}")
        else:
            review, _, diff = run_review(ctx["owner"], ctx["repo"], ctx["number"])
            footer = f"\n\n---\n*Reviewed by [claude-pr-reviewer](https://github.com/indoor47/claude-pr-reviewer) using {CLAUDE_MODEL}*"

            valid_lines = parse_diff_for_lines(diff)
            cleaned_review, inline_comments = parse_inline_comments(review, valid_lines)
            event = parse_verdict(cleaned_review)
            review_body = f"{REVIEW_MARKER}\n## Claude Code Review\n\n{cleaned_review}{footer}"

            existing_review_id = find_existing_pr_review(ctx["owner"], ctx["repo"], ctx["number"])
            if existing_review_id:
                print("  Updating existing PR review...")
                patch_url = f"https://api.github.com/repos/{ctx['owner']}/{ctx['repo']}/pulls/{ctx['number']}/reviews/{existing_review_id}"
                github_patch(patch_url, {"body": review_body})
                print(f"  Review updated on PR #{ctx['number']}")
            else:
                print(f"  Posting PR review ({event}) with {len(inline_comments)} inline comment(s)...")
                post_pr_review(ctx["owner"], ctx["repo"], ctx["number"], ctx["head_sha"], review_body, event, inline_comments)
                print(f"  Review posted on PR #{ctx['number']}")

    else:
        if len(sys.argv) < 2:
            print("Usage: python pr_reviewer.py <github-pr-url> [--paid]")
            print("       python pr_reviewer.py https://github.com/owner/repo/pull/123")
            print("       python pr_reviewer.py https://github.com/owner/repo/pull/123 --paid")
            sys.exit(1)

        pr_url = sys.argv[1]

        if HOSTED_API_KEY:
            print(f"Calling hosted review API...")
            review = hosted_review(pr_url)
        else:
            owner, repo, pr_number = parse_pr_url(pr_url)
            print(f"Fetching PR #{pr_number} from {owner}/{repo}...")
            review, _, _ = run_review(owner, repo, pr_number)

        print("\n" + "=" * 60)
        print(review)
        print("=" * 60)
        tier = "Paid (claude-opus-4-6)" if use_paid else "Free"
        print(f"\nReviewed: {pr_url} ({tier} tier)")
        print("=" * 60)


if __name__ == "__main__":
    main()
