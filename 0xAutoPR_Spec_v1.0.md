  
**0xAutoPR**

Automated PR Review & Auto-Fix System

| Version | 1.0.0 |
| :---- | :---- |
| **Date** | June 2026 |
| **Status** | Draft — For Review |
| **Stack** | 100% Free / Open Source |

# **1\. Executive Summary**

0xAutoPR is an open-source, multi-agent AI system that automatically reviews pull requests, identifies bugs and quality issues, generates code fixes, writes tests, and opens a corrective PR — all without human intervention. It closes the loop that every existing tool leaves open: the gap between “found a problem” and “fixed the problem.”

| Core Value Proposition Every competitor (CodeRabbit, Greptile, Graphite, Claude Code Review) stops at commenting. 0xAutoPR is the first system to go all the way to a committed, tested fix pushed back to the repository. |
| :---- |

The system is built entirely on free-tier and open-source resources: Google Gemini Flash (1M token context, free tier), Groq (free fast inference), LangGraph (open-source orchestration), GitHub API (free), and ChromaDB (local vector store). Total infrastructure cost: $0.

## **1.1 Problem Statement**

Code review is the hidden bottleneck in modern engineering. Senior developers spend an estimated 4–8 hours per week reviewing PRs. Existing AI tools reduce that burden partially — they comment on issues — but the developer still has to:

* Read and understand the AI’s comment

* Decide whether the comment is valid

* Write the fix themselves

* Write tests to validate the fix

* Push the updated commit

0xAutoPR eliminates all five steps for the majority of reviewable issues.

## **1.2 Key Differentiators**

| Capability | Competitors | 0xAutoPR |
| :---- | :---- | :---- |
| Auto-fix \+ commit | None | Yes — core feature |
| Full repo context | Greptile only | Yes (1M token window) |
| Test generation | None | Yes — per fix |
| Multi-platform support | Partial | GitHub, GitLab, Bitbucket |
| Cost | $15–$49/user/month | $0 (free tier stack) |
| Learning from feedback | None | Yes — per-team memory |

# **2\. System Architecture**

0xAutoPR is built as a multi-agent orchestration pipeline. Each agent has a single responsibility and communicates through a shared state object managed by LangGraph. The orchestrator coordinates flow and handles retries, failures, and escalations.

## **2.1 High-Level Flow**

1. A PR is opened or updated on GitHub/GitLab/Bitbucket

2. A webhook fires to the 0xAutoPR server (or GitHub Actions runner)

3. The Orchestrator Agent receives the event and initializes shared state

4. The Code Reader Agent fetches the diff and indexes the full repo

5. The Review Agent runs parallel analysis passes (bugs, security, quality)

6. The Fix Writer Agent generates code changes for each issue found

7. The Patch Generator Agent produces git-ready diffs per fix

8. The Test Writer Agent writes unit/integration tests and runs them

9. The Orchestrator validates: all tests pass, patch is clean, confidence threshold met

10. The PR Opener Agent commits the fix and opens a new PR (or pushes to the branch)

## **2.2 Agent Definitions**

### **2.2.1 Orchestrator Agent**

Role: Master coordinator. Owns the pipeline state machine, decides execution order, handles retries (max 3), escalates to human review if confidence \< threshold.

* Input: PR event payload (repo URL, branch, diff URL, PR metadata)

* Output: Final pipeline status (fixed / escalated / skipped)

* Model: Gemini 2.0 Flash (free tier)

* Key decisions: which issues to fix, whether to retry, whether to escalate

### **2.2.2 Code Reader Agent**

Role: Builds complete context. Fetches the PR diff via GitHub API, clones or shallow-fetches the repo, chunks and embeds files into ChromaDB, and constructs a structured representation of the changed code with full dependency context.

* Input: Repo URL, branch name, PR diff

* Output: Structured context object — changed files, affected functions, dependency graph

* Model: Gemini 2.0 Flash (1M token context window handles full repos)

* Tools: GitHub API, GitPython, ChromaDB, Google text-embedding-004

### **2.2.3 Review Agent**

Role: Identifies issues. Runs three parallel sub-passes using the context object from the Code Reader. Each sub-pass focuses on a different category and returns structured findings.

* Sub-pass A — Bug detection: logic errors, null pointer risks, off-by-one, race conditions

* Sub-pass B — Security: injection, hardcoded secrets, improper auth, insecure deps

* Sub-pass C — Quality: code smell, missing error handling, performance anti-patterns

* Output: Ranked list of issues with severity (critical / high / medium / low), file, line, explanation

* Model: Groq \+ Llama 3.3 70B (fast, free tier)

### **2.2.4 Fix Writer Agent**

Role: Generates code fixes. For each issue above the severity threshold, generates a corrected code snippet with explanation. Works file-by-file to avoid context collisions.

* Input: Issue list from Review Agent \+ full file context from Code Reader

* Output: Fix objects (file path, original lines, replacement lines, explanation)

* Model: Gemini 2.0 Flash (code generation quality, large context)

* Constraint: Only modifies lines directly related to the issue. No style refactoring.

### **2.2.5 Patch Generator Agent**

Role: Produces git-ready artifacts. Applies each fix to the working tree and generates a unified diff patch. Groups fixes into atomic commits by file/module.

* Input: Fix objects from Fix Writer Agent

* Output: Git patch files, commit messages (conventional commit format)

* Tools: GitPython, difflib

* Model: Gemini Flash (for commit message generation only)

### **2.2.6 Test Writer Agent**

Role: Validates fixes. Writes unit tests for each fix, targeting the specific function or module changed. Runs tests in a sandboxed subprocess and reports pass/fail. On failure, returns a structured error report to the Orchestrator.

* Input: Fix objects \+ existing test files from the repo

* Output: New test files, test run results (pass/fail/error)

* Model: Groq \+ Llama 3.3 70B

* Execution: Python subprocess with pytest / jest / go test depending on detected language

### **2.2.7 PR Opener Agent**

Role: Delivers the fix. Pushes the commit to a new branch (format: 0xautoPR/fix-{pr\_number}-{short\_hash}), then opens a pull request against the original PR’s base branch with a structured description.

* Input: Validated patches \+ test results \+ issue summaries

* Output: New PR URL, branch name, commit SHA

* Tools: GitHub API (PyGitHub), GitPython

* PR description template: issues found, fixes applied, tests added, confidence score

# **3\. Technology Stack (100% Free)**

| Component | Technology | Why | Cost |
| :---- | :---- | :---- | :---- |
| LLM — primary | Gemini 2.0 Flash | 1M token context, fast, free tier | $0 |
| LLM — fast inference | Groq \+ Llama 3.3 70B | Ultra-fast, free tier (14k TPM) | $0 |
| LLM — fallback | OpenRouter free tier | Route to best free model | $0 |
| Local models | Ollama \+ Qwen2.5-Coder | Offline, no rate limits | $0 |
| Orchestration | LangGraph | Stateful multi-agent graphs | $0 (OSS) |
| Embeddings | Google text-embedding-004 | Free via Gemini API | $0 |
| Vector store | ChromaDB | Local, no cloud needed | $0 (OSS) |
| Repo access | GitHub API (PyGitHub) | Read/write PRs, push commits | $0 |
| Git operations | GitPython | Local git operations | $0 (OSS) |
| CI trigger | GitHub Actions | Free for public repos | $0 |
| Local webhook | ngrok free tier | Expose local server for testing | $0 |
| Language | Python 3.11+ | Best AI/ML ecosystem | $0 |

# **4\. Functional Requirements**

## **4.1 Core Pipeline (Must Have)**

| ID | Requirement | Priority | Agent |
| :---- | :---- | :---- | :---- |
| FR-01 | System must receive PR webhook events from GitHub, GitLab, Bitbucket | P0 | Orchestrator |
| FR-02 | System must fetch full PR diff and changed file contents | P0 | Code Reader |
| FR-03 | System must index the entire repository into a vector store for context retrieval | P0 | Code Reader |
| FR-04 | System must detect bugs, security issues, and quality problems in changed code | P0 | Review |
| FR-05 | System must generate valid code fixes for detected issues | P0 | Fix Writer |
| FR-06 | System must produce git-compatible unified diff patches | P0 | Patch Generator |
| FR-07 | System must write unit tests for each generated fix | P0 | Test Writer |
| FR-08 | System must execute tests and verify they pass before committing | P0 | Test Writer |
| FR-09 | System must push fix to a new branch and open a PR with full description | P0 | PR Opener |
| FR-10 | System must escalate to human review when confidence score \< 0.75 | P0 | Orchestrator |

## **4.2 Quality & Safety (Must Have)**

| ID | Requirement | Priority | Agent |
| :---- | :---- | :---- | :---- |
| FR-11 | Fix must only modify lines directly related to the identified issue | P0 | Fix Writer |
| FR-12 | System must not push any fix where tests fail after max 3 retry attempts | P0 | Orchestrator |
| FR-13 | System must include issue summary, fix explanation, and test results in PR description | P0 | PR Opener |
| FR-14 | System must support Python, JavaScript/TypeScript, Go, Java, and Ruby codebases | P1 | All agents |
| FR-15 | System must detect language automatically and select appropriate test runner | P1 | Test Writer |
| FR-16 | System must skip fix generation and comment-only for issues with confidence \< 0.6 | P1 | Orchestrator |

## **4.3 Learning & Personalization (Nice to Have — v2)**

* FR-17: System learns from developer accept/dismiss actions on generated PRs

* FR-18: System builds per-repo style and convention profiles over time

* FR-19: System reduces false positive rate per team based on historical feedback

# **5\. Non-Functional Requirements**

## **5.1 Performance**

| Metric | Target | Notes |
| :---- | :---- | :---- |
| End-to-end pipeline time | \< 3 minutes | For PRs with \< 500 lines changed |
| Repo indexing time | \< 60 seconds | For repos \< 100K lines |
| LLM calls per PR | \< 12 total | To stay within free tier limits |
| False positive rate | \< 15% | Target \< CodeRabbit’s 2/run |
| Bug catch rate | \> 60% | Above CodeRabbit’s 44% |
| Test pass rate on fixes | \> 80% | Before opening PR |

## **5.2 Reliability**

* System must handle GitHub API rate limits with exponential backoff

* System must handle LLM rate limits by queuing and retrying after cooldown

* System must never corrupt the original PR branch

* All fixes go to isolated branches only — never force-push to the source branch

* System must log all agent decisions and outputs to a structured audit trail

## **5.3 Security**

* GitHub tokens stored as environment variables or GitHub Secrets — never in code

* Webhook payloads must be verified using HMAC-SHA256 signature validation

* All LLM prompts must be sanitized to prevent prompt injection from malicious code comments

* No user code is sent to third-party services beyond the configured LLM providers

* The fix writer agent must not execute arbitrary code from the PR diff

# **6\. Project Structure**

Recommended monorepo layout for 0xAutoPR:

| Repository Layout 0xAutoPR/  agents/    orchestrator.py       — LangGraph state machine    code\_reader.py        — repo fetch, embed, chunk    review\_agent.py       — bug/security/quality passes    fix\_writer.py         — code fix generation    patch\_generator.py    — git diff production    test\_writer.py        — test generation \+ execution    pr\_opener.py          — commit \+ open PR  core/    state.py              — shared pipeline state schema    config.py             — free-tier model config    github\_client.py      — GitHub API wrapper    vector\_store.py       — ChromaDB interface  triggers/    webhook\_server.py     — Flask server for webhooks    github\_actions.yml    — CI trigger definition  tests/    test\_agents.py    test\_pipeline.py  .env.example  requirements.txt  README.md |
| :---- |

# **7\. Pipeline State Schema**

All agents share a single state object managed by LangGraph. This is the contract between agents.

| PipelineState (core/state.py) {  pr\_url: str,  repo\_url: str,  base\_branch: str,  head\_branch: str,  diff\_raw: str,  changed\_files: List\[ChangedFile\],  repo\_context: VectorStoreRef,  issues: List\[Issue\],          — output of Review Agent  fixes: List\[Fix\],             — output of Fix Writer  patches: List\[Patch\],         — output of Patch Generator  test\_results: List\[TestResult\],  confidence\_score: float,      — 0.0 to 1.0  retry\_count: int,  status: Literal\['running','fixed','escalated','skipped','failed'\],  output\_pr\_url: Optional\[str\],  audit\_log: List\[LogEntry\]} |
| :---- |

# **8\. Development Milestones**

| Phase | Timeline | Deliverables | Status |
| :---- | :---- | :---- | :---- |
| 0 | Week 1 | Repo setup, LangGraph skeleton, state schema, GitHub webhook receiver | Not started |
| 1 | Week 2–3 | Code Reader Agent: diff fetch, repo clone, ChromaDB indexing, embedding | Not started |
| 2 | Week 4–5 | Review Agent: 3 parallel sub-passes, structured issue output, severity scoring | Not started |
| 3 | Week 6–7 | Fix Writer Agent: code fix generation, context-aware patching | Not started |
| 4 | Week 8 | Patch Generator: unified diffs, atomic commits, conventional commit messages | Not started |
| 5 | Week 9–10 | Test Writer Agent: test generation, pytest/jest/go test runner integration | Not started |
| 6 | Week 11 | PR Opener Agent: branch push, PR creation with full description template | Not started |
| 7 | Week 12 | End-to-end integration test on 20 real OSS PRs, performance tuning, v1.0 tag | Not started |

# **9\. Risks & Mitigations**

| Risk | Severity | Mitigation |
| :---- | :---- | :---- |
| Gemini free tier rate limits (15 RPM) slow pipeline | High | Queue PRs, process sequentially, add Groq as fast-path fallback |
| Fix writer generates incorrect code that passes tests | High | Confidence scoring \+ min 2 test cases per fix \+ human escalation threshold |
| Large monorepos exceed 1M token context | Medium | Chunked retrieval via ChromaDB, only load relevant files via RAG |
| Generated tests are trivial and don’t catch regressions | Medium | Test quality scoring: require branch coverage \> 60% per fix |
| GitHub Actions free minutes exhausted on public repo | Low | Lightweight trigger only; heavy work runs on local runner or Codespace |
| LLM generates fix that introduces new security issue | High | Re-run Review Agent on the generated fix before committing |

# **10\. Out of Scope (v1.0)**

* GUI or web dashboard

* Multi-language fix support beyond Python, JS/TS, Go, Java, Ruby

* Auto-merge of the fix PR (human approval required for merge)

* Per-team learning and feedback loop (planned for v2)

* Self-hosted LLM serving at scale

* Billing or SaaS features

| Next Step Start with Phase 0: set up the LangGraph skeleton, define the PipelineState schema, and wire up the GitHub webhook receiver. That’s the foundation everything else builds on. |
| :---- |

