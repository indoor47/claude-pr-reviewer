# claude-pr-reviewer

AI code review on every pull request. Uses Claude to post structured feedback as a PR comment.

Works as a **GitHub Action** (automated on every PR) or a **CLI tool** (review any PR from your terminal).

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
      - uses: indoor47/claude-pr-reviewer@main
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
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

Diffs over 40,000 characters are truncated. For large PRs, split into smaller ones.

## Cost

Each review costs roughly $0.003–0.02 with Sonnet (default) or $0.01–0.05 with Opus. Haiku is cheapest at ~$0.001 per review. A team running 20 PRs/day spends about $1–2/month.

## License

MIT

---

Built by [Adam](https://dev.to/adamai).
