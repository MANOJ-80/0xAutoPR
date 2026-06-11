# 0xAutoPR

Automated PR review and auto-fix system. 0xAutoPR reviews pull requests, identifies bugs and quality issues, generates code fixes, writes tests, and opens a corrective PR — closing the loop between "found a problem" and "fixed the problem."

Built entirely on free-tier and open-source resources: **Gemini Flash**, **Groq**, **LangGraph**, **ChromaDB**, and the **GitHub API**.

## Architecture

```
PR Webhook → Orchestrator → Code Reader → Review Agent (3 parallel passes)
                ↓                              ↓
           PR Opener ← Test Writer ← Patch Generator ← Fix Writer
```

| Agent | Role |
|-------|------|
| Orchestrator | LangGraph state machine, retries, escalation |
| Code Reader | Fetch diff, clone repo, ChromaDB indexing |
| Review Agent | Bug / security / quality parallel analysis |
| Fix Writer | Generate targeted code fixes |
| Patch Generator | Unified diffs, conventional commits |
| Test Writer | Generate and run tests (pytest/jest/go test) |
| PR Opener | Push fix branch, open corrective PR |

## Quick Start

### 1. Install

```bash
git clone https://github.com/your-org/0xAutoPR.git
cd 0xAutoPR
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
```

### 2. Run webhook server

```bash
python main.py serve
```

Expose locally with ngrok:

```bash
ngrok http 8080
```

Configure your GitHub webhook:
- **URL:** `https://<ngrok-id>.ngrok.io/webhook/github`
- **Content type:** `application/json`
- **Events:** Pull requests
- **Secret:** same as `GITHUB_WEBHOOK_SECRET`

### 3. Run on a specific PR (CLI)

```bash
python main.py run \
  --pr-url "https://github.com/org/repo/pull/123" \
  --pr-number 123 \
  --repo-url "https://github.com/org/repo" \
  --repo-full-name "org/repo" \
  --base-branch main \
  --head-branch feature-branch
```

### 4. GitHub Actions

Copy `triggers/github_actions.yml` to `.github/workflows/0xautopr.yml` in your target repo. Add secrets:

- `GEMINI_API_KEY`
- `GROQ_API_KEY` (optional but recommended)

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_TOKEN` | Yes | PAT with `repo` scope |
| `GITHUB_WEBHOOK_SECRET` | Yes (prod) | HMAC secret for webhook validation |
| `GEMINI_API_KEY` | Yes* | Google AI Studio key |
| `GROQ_API_KEY` | Recommended | Groq API key for fast review |
| `DRY_RUN` | No | Skip pushing fix PRs when `true` |

*At least one LLM provider required. Falls back to Ollama if configured.

## Thresholds

Configured in `core/config.py`:

| Threshold | Default | Behavior |
|-----------|---------|----------|
| `fix_min_confidence` | 0.6 | Skip auto-fix below this |
| `escalate_confidence` | 0.75 | Escalate to human review below this |
| `max_retries` | 3 | Retry fix generation on test failure |

## Project Structure

```
0xAutoPR/
├── agents/           # Multi-agent pipeline
├── core/             # State, config, GitHub, vector store, LLM
├── triggers/         # Webhook server + GitHub Actions
├── tests/            # Unit and integration tests
├── main.py           # CLI entry point
└── requirements.txt
```

## Running Tests

```bash
pytest tests/ -v
```

## Security

- Webhook payloads verified via HMAC-SHA256
- API keys stored in environment variables only
- LLM prompts sanitized against injection from code comments
- Fixes pushed to isolated branches only — never force-push to source branch

## License

MIT
