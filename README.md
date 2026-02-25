# claude-pr-reviewer

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![GitHub Stars](https://img.shields.io/github/stars/indoor47/claude-pr-reviewer?style=social)](https://github.com/indoor47/claude-pr-reviewer/stargazers)

Claude reviews your pull requests and posts structured feedback — catching logic bugs, security issues, and style problems before merge. Posts as a formal GitHub PR review with inline comments on specific lines.

**Two modes**: GitHub Action (runs on every PR automatically) or CLI (review any PR from your terminal). Zero dependencies, Python 3.8+ stdlib only.

## GitHub Action (recommended)

Add this to `.github/workflows/pr-review.yml`:

```yaml
name: Claude PR Review
on:
  pull_request:
    types: [opened, synchronize]

permissions:
  pull-requests: write
  contents: read

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: indoor47/claude-pr-reviewer@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
```

Optional: tune strictness or model:

```yaml
      - uses: indoor47/claude-pr-reviewer@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          strictness: lenient    # lenient | balanced (default) | strict
          model: claude-haiku-4-5-20251001  # cheapest option, ~$0.001/review
```

That's it. Every PR gets a review comment like this:

```
## Claude Code Review

## Summary
This PR adds token bucket rate limiting to the /login and /register
endpoints to prevent brute force attacks...

## Issues Found

### Critical (must fix before merge)
- `auth/middleware.py:34`: Rate limit state is stored in-process memory.
  This means limits reset on every deploy and don't work across multiple
  server instances. Use Redis or a shared store.

### Major (should fix)
- The rate limit window resets on each request rather than using a
  sliding window, making it easy to bypass with careful timing.

### Minor (consider fixing)
- Variable `ratelimit_max` (line 12) should be `RATE_LIMIT_MAX` per PEP8.

## Security
The current implementation can be bypassed by rotating IPs. Consider
combining with user-based limiting in addition to IP-based.

## Overall Verdict
REQUEST CHANGES — the in-memory state is a correctness bug that will
cause silent failures in production.
```

### Action inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `anthropic_api_key` | yes | — | Your Anthropic API key |
| `model` | no | `claude-sonnet-4-6` | Claude model (`claude-sonnet-4-6`, `claude-opus-4-6`, `claude-haiku-4-5-20251001`) |
| `max_tokens` | no | `4096` | Max response tokens |
| `strictness` | no | `balanced` | `lenient` (critical+security only), `balanced` (default), `strict` (adds test coverage, docs, tech debt) |
| `ignore_patterns` | no | — | Comma-separated globs for files to skip (e.g. `"*.md,*.lock"`) |

### Config file (optional)

Instead of putting everything in your workflow YAML, drop a `.pr-reviewer.yml` in your repo root:

```yaml
# .pr-reviewer.yml
strictness: balanced      # lenient | balanced | strict
model: claude-sonnet-4-6  # or claude-haiku-4-5-20251001 / claude-opus-4-6
max_tokens: 4096
walkthrough: true
ignore_patterns:
  - "*.lock"
  - "dist/**"
  - "*.min.js"
  - "migrations/**"
```

The config file is picked up automatically — no changes to your workflow needed. Environment variables and Action inputs always take precedence over the config file, so you can override per-environment without editing the file.

### Setup

1. Get an API key at [console.anthropic.com](https://console.anthropic.com)
2. Add it as a repository secret: Settings → Secrets → `ANTHROPIC_API_KEY`
3. Add the workflow file above
4. Open a PR — review appears automatically

## CLI usage

```bash
git clone https://github.com/indoor47/claude-pr-reviewer
cd claude-pr-reviewer

export ANTHROPIC_API_KEY=sk-ant-...
export GITHUB_TOKEN=ghp_...   # optional, for private repos

python pr_reviewer.py https://github.com/owner/repo/pull/123
```

No dependencies. Python 3.8+, stdlib only.

## How it works

1. Reads PR metadata and diff from GitHub API
2. Sends to Claude with a structured review prompt
3. Posts the review as a PR comment (Action) or prints to stdout (CLI)

Reviews are posted using GitHub's [PR Reviews API](https://docs.github.com/en/rest/pulls/reviews) — the same mechanism human reviewers use. Inline comments appear directly on the relevant lines of code, not as a wall of text. On subsequent pushes, the Action updates the existing review rather than creating a new one.

**What it catches**: logic bugs, security issues (injection, auth flaws, data exposure), missing error handling, performance problems, style/naming.

Diffs over 40,000 characters are truncated. For large PRs, split into smaller ones.

## Cost

Self-hosted: each review costs roughly $0.003–0.02 with Sonnet (default) or $0.01–0.05 with Opus. Haiku is cheapest at ~$0.001 per review. A team running 20 PRs/day spends about $1–2/month.

## vs. Other Tools

| | **claude-pr-reviewer** | CodeRabbit | GitHub Copilot Review | qodo/pr-agent |
|--|------------------------|------------|----------------------|---------------|
| **Model** | Claude (you choose) | Proprietary | GPT-4o | GPT-4 / Claude |
| **Private repos free** | ✓ | ✗ (paid plan) | ✗ ($19+/mo) | ✓ (self-hosted) |
| **Setup** | 2 min, 1 secret | GitHub App | Org-level setting | Multiple env vars |
| **Zero dependencies** | ✓ | Cloud-only | Cloud-only | Python deps |
| **Strictness control** | ✓ (lenient/balanced/strict) | ✗ | ✗ | Limited |
| **Cost per review** | ~$0.001–0.05 (shown per run) | Opaque | ~$0.04+ after quota | ~$0.001–0.05 |
| **Inline comments** | ✓ | ✓ | ✓ | ✓ |
| **No 3rd-party data sharing** | ✓ (Anthropic API only) | ✗ (CodeRabbit servers) | ✗ (GitHub/OpenAI) | ✓ (self-hosted) |

**Why CodeRabbit users switch**: CodeRabbit is verbose by design — it generates the highest comment volume of any tool, which teams report as noise fatigue. claude-pr-reviewer's `lenient` mode gives you only blocking issues, and `strict` mode adds depth when you need it.

## Troubleshooting

**Action fails with "Resource not accessible"**
- Check that `ANTHROPIC_API_KEY` secret is set (Settings → Secrets → New)
- Ensure workflow has `pull-requests: write` permission

**Review comment not appearing**
- Check Action logs in PR "Checks" tab
- If diff > 40KB, PR is too large — split into smaller PRs
- Verify ANTHROPIC_API_KEY is valid at [console.anthropic.com](https://console.anthropic.com)

**Review is vague or not technical enough**
- Switch model to Opus (higher accuracy): add `model: claude-opus-4-6` to inputs
- This increases cost to $0.01–0.05/review but catches deeper issues

**Want to skip certain files?**
- Use `ignore_patterns`: e.g., `"*.md,*.lock,test/**"`
- Useful for docs, lockfiles, generated code

## FAQ

**Q: Does this expose my code to Claude/Anthropic?**
A: Only if you use the hosted tier (coming soon). Self-hosted mode sends diffs directly to the Anthropic API, which they do not store or train on (see [privacy policy](https://www.anthropic.com/legal/privacy)).

**Q: Can I use this on private repos?**
A: Yes. Provide a `GITHUB_TOKEN` with `repo` scope (Action or CLI). Diff is still only sent to Anthropic, never to 3rd parties.

**Q: How accurate is the review?**
A: Depends on PR size and complexity. Sonnet handles most reviews well. For critical or architecturally complex PRs, switch to Opus.

**Q: Why Python 3.8+?**
A: Oldest version with solid urllib/json support and type hints. No external deps = no supply chain risk.

## License

MIT

---

Issues and PRs welcome · MIT License
