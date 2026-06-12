# 0xAutoPR

Automated PR review and auto-fix system. 0xAutoPR reviews pull requests, identifies bugs and quality issues, generates code fixes, writes tests, and autonomously opens a corrective PR — closing the loop between "found a problem" and "fixed the problem."

Built entirely for the **NVIDIA NIM Free Tier**, leveraging **Llama 3.1 70B**, **ChromaDB**, **LangGraph**, and **SQLite LLM Caching** to operate as a self-healing, rate-limit resistant autonomous engineer.

## Architecture

```text
PR Webhook → Orchestrator → Code Reader → Review Agent (Bug, Security, Quality)
                ↓                              ↓
           PR Opener ← Test Writer ← Patch Generator ← Fix Writer
```

| Agent               | Role                                                                 |
| ------------------- | -------------------------------------------------------------------- |
| **Orchestrator**    | LangGraph state machine, retries, LLM caching, rate-limit mitigation |
| **Code Reader**     | Fetch diff, clone repo, ChromaDB codebase indexing (via NV-Embed)    |
| **Review Agent**    | Comprehensive, sequential analysis over the PR diff                  |
| **Fix Writer**      | Generate targeted, localized code fixes for identified issues        |
| **Patch Generator** | Converts LLM suggestions into strict, applicable git unified diffs   |
| **Test Writer**     | Generates and executes local unit tests to validate the fix          |
| **PR Opener**       | Pushes the fix branch and opens a corrective PR via the GitHub API   |

## Key Features

- **100% Free-Tier Architecture**: Hard-optimized to survive strict API rate limits using a global token bucket and intelligent cooldowns.
- **SQLite LLM Caching**: Instantly bypasses redundant LLM API requests on retries by hashing prompts, turning 3-minute generation loops into 10-millisecond cache hits.
- **Self-Healing State Machine**: Driven by LangGraph, if a generated fix fails the unit tests, the system automatically loops back, attaches the error logs to the prompt, and tries again.
- **Asymmetric Vector RAG**: Fully embeds the target repository using `nvidia/nv-embedcode-7b-v1` to give the agents deep architectural context before reviewing a PR.

## Quick Start

### 1. Install

```bash
git clone https://github.com/MANOJ-80/0xAutoPR.git
cd 0xAutoPR
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
```

### 2. Configure API Keys

The system requires only two tokens to operate:

- `NVIDIA_API_KEY`: Get your free NIM key from build.nvidia.com
- `GITHUB_TOKEN`: A Personal Access Token with `repo` scope to interact with Pull Requests.

### 3. Run on a specific PR (CLI)

You can manually trigger a review for any PR locally:

```bash
python main.py run \
  --pr-url "https://github.com/org/repo/pull/123" \
  --pr-number 123 \
  --repo-url "https://github.com/org/repo" \
  --repo-full-name "org/repo" \
  --base-branch main \
  --head-branch feature-branch
```

### 4. Continuous Integration (GitHub Actions)

To run this autonomously on every new PR, copy `triggers/github_actions.yml` to `.github/workflows/0xautopr.yml` in your target repository.

Make sure to add `NVIDIA_API_KEY` to your repository's GitHub Secrets.

## Configuration & Thresholds

Configured in `core/config.py`:

| Threshold             | Default | Behavior                                                  |
| --------------------- | ------- | --------------------------------------------------------- |
| `fix_min_confidence`  | 0.6     | Skip auto-fix generation for issues below this confidence |
| `escalate_confidence` | 0.75    | Escalate to human review if the fix is uncertain          |
| `max_retries`         | 3       | LangGraph retry limit on test suite failures              |

## Security

- Webhook payloads verified via HMAC-SHA256
- API keys stored in environment variables only
- LLM prompts sanitized against injection from code comments
- Fixes pushed to isolated branches only — never force-pushes to the source branch

## License

MIT
