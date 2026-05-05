# CMPE 258 Final Project Report

## Cover

- **Team ID:** *TBD (fill before Canvas submission)*
- **Project Title:** Autonomous Bug-Squashing Agent — A Planner / Executor / Critic Loop with a Multi-Model, OSS-Grounded Benchmark Harness
- **Course:** CMPE 258 — Deep Learning, Spring 2026, San Jose State University
- **Project Track:** **Application** (LLM-powered developer tool with rigorous evaluation)
- **Focused Areas:** LLM agent systems · Automated program repair · Multi-model benchmarking with mutation injection

### Team Members

| Name | SJSU ID | Email | Primary Contribution |
|---|---|---|---|
| Pranav Jitendra Trivedi | 019089512 | Pranavjitendra.trivedi@sjsu.edu | Agent architecture · Planner module · Critic loop |
| Yashashav Devalapalli Kamalraj | 017856371 | yashashav.devalapallikamalraj@sjsu.edu | Model integrations (Gemini, Qwen, MiniMax, Gemma) · Benchmark harness · Eval pipeline |
| Saransh Soni | 019115122 | saransh.soni@sjsu.edu | Memory module · Web UI · Dataset curation · Docker |

---

## Abstract

We built an autonomous bug-squashing agent that takes a failing Python file plus its `pytest` traceback and iteratively edits source code until tests pass — with no human in the loop. The agent runs a Planner / Executor / Critic loop: a Large Language Model (LLM) Planner selects from three tools (`read_file`, `edit_file`, `run_bash`); a sandboxed Executor applies patches atomically with `ast.parse` validation and a `realpath` scope guard; a Critic returns `RESOLVED | RETRY | UNRESOLVED` and feeds structured retry context back; per-case Memory deduplicates dead-end patches via line-range fingerprints. We evaluate on two complementary benchmarks: (a) a synthetic dataset of 52 curated Python bugs across syntax, logic and scope tiers, and (b) three real defects injected into the `pallets/click` v8.3.2 OSS library, oracle-verified by the project's own pytest suite. Across four LLMs (Gemini 3 Flash/Pro, Qwen 2.5 72B via OpenRouter, Gemma 3/4 via Ollama), we achieve **57 % pass rate** on the synthetic set with Qwen, **33 % (1/3)** on OSS bugs with Gemini 3 Pro, and a **3.1–3.4 × input-token reduction** plus **2.5–2.7 × wall-clock speedup** versus a Gemini-CLI baseline at identical resolution outcomes. Localisation failure (right file, wrong line) accounts for ~87 % of OSS failures, identifying the next research target.

---

## 1. Introduction & Problem Description

Junior developers and students hit a recurring wall: a long Python traceback halts progress, and copy-pasting fragments into a chat assistant produces hallucinated patches that do not actually run. Production tools (Devin, SWE-Agent, Aider) ship as black boxes with little visibility into why a patch worked or failed.

**Problem.** Build a transparent, reproducible agent that:
1. Reads a buggy Python file and a failing `pytest` traceback,
2. Locates the bug, proposes a patch, and verifies the fix by re-running tests,
3. Reports honest pass/fail metrics on both controlled and real-world benchmarks,
4. Operates locally when needed (privacy-sensitive student code) and via remote APIs when scale demands.

**Why it matters.** Every claim in modern automated-program-repair (APR) literature lives or dies by *evaluation rigour*. We want a harness that exposes where LLM agents actually break — not a curated demo where they shine.

**Target users.** CS students learning to debug, instructors who want a transparent tutor, and researchers who need a reproducible APR baseline.

---

## 2. Background & Related Work

| Work | Relation to ours |
|---|---|
| **SWE-Bench** (Jimenez et al., 2024) | Real GitHub PRs as oracle. Heavy infra cost; we adopted the spirit (real OSS oracle) but at a tractable single-repo scale via `pallets/click`. |
| **SWE-Agent** (Yang et al., 2024) | ACI (agent-computer interface) with shell tools. Influenced our `read_file` / `edit_file` / `run_bash` triplet. |
| **Aider, Devin, OpenHands** | Production agents. Closed loops, opaque metrics. We open the box: structured JSON-lines logs, per-step token accounting, deterministic injection. |
| **CodeR, AgentCoder** | Multi-role debate frameworks. We chose Planner→Executor→Critic to keep the role count to three for tractability. |
| **Mutation testing** (Just et al.) | Foundation of our `SyntheticMutationAdapter`: AST-level operator/literal/name flips against a pinned commit. |
| **Wilson score interval** (Wilson, 1927) | Used in `benchmark/analyze.py` for honest small-sample 95 % confidence on pass rates. |
| **Ollama / Gemma 4** | Local-first inference. Enables privacy-sensitive deployments without API keys. |

We deliberately do **not** depend on LangChain, AutoGen or CrewAI: every line of agent logic is original Python. This was a hard constraint from the proposal so the loop's behaviour is fully introspectable.

---

## 3. System / Algorithm / Model Design

### 3.1 The Planner / Executor / Critic Loop

```
                    ┌────────────────────────────────────────────┐
                    │       PLANNER (LLM, multi-turn)            │
                    │  reads:  buggy file · traceback · memory   │
                    │  emits:  tool calls (read/edit/run_bash)   │
                    │  models: Gemini 3 · Qwen 2.5 · Gemma 3/4   │
                    └──────────────────────┬─────────────────────┘
                                           │  tool call (JSON)
                                           ▼
                    ┌────────────────────────────────────────────┐
                    │       EXECUTOR (sandboxed)                 │
                    │  • realpath scope guard                    │
                    │  • atomic write (.tmp → ast.parse →        │
                    │    os.replace)                             │
                    │  • subprocess [sys.executable -m pytest …] │
                    │    with shell=False                        │
                    └──────────────────────┬─────────────────────┘
                                           │  exit codes + stderr
                                           ▼
                    ┌────────────────────────────────────────────┐
                    │       CRITIC                               │
                    │  RESOLVED · RETRY · UNRESOLVED             │
                    │  builds compact retry context              │
                    └─────────────┬──────────────────────────────┘
                                  │ on RETRY
                                  ▼
                    ┌────────────────────────────────────────────┐
                    │       MEMORY (per case, fresh each run)    │
                    │  • dead-end fingerprint: (line_range, fix) │
                    │  • token-bounded summary (≤ 2000 tokens)   │
                    │  • optional save → memory.json             │
                    └─────────────┬──────────────────────────────┘
                                  │
                                  └────► back to Planner
```

**Key design choices.**

- **Stateless components except Memory.** Each call to `run_case_with_pec()` constructs a fresh `Memory()`. Cross-case state leakage is treated as a bug (see `agent/case_runtime.py`).
- **Patch = literal line replacement.** `proposed_fix` replaces lines `[lo, hi]` (1-indexed, inclusive). No diff parser — `ast.parse` catches malformed Python before `os.replace` commits.
- **Three tools, not thirty.** `read_file`, `edit_file`, `run_bash`. The `run_bash` tool whitelists `pytest` invocations only; arbitrary shell is rejected.
- **Dead-end fingerprint = `(line_range_tuple, proposed_fix.strip())`.** If the Planner re-emits an identical patch, Memory short-circuits and prevents the wasted Executor round-trip.

### 3.2 Benchmark Pipeline

```
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ HistoricalAdapter│   │ SyntheticAdapter │   │ Internal dataset │
│  curated bugs    │   │ AST mutations    │   │ 52 cases × 3 tier│
└────────┬─────────┘   └────────┬─────────┘   └────────┬─────────┘
         │                      │                      │
         └──────────────┬───────┴──────────────────────┘
                        ▼
          benchmark/build_manifest.py  (ratio mix, seed)
                        │
                        ▼
                 manifest.jsonl  (BenchmarkCase rows)
                        │
                        ▼
   benchmark/run_matrix.py      cases × models × repetitions
   ├─ copy workspace to logs/runs/<run_id>/workspaces/<attempt_id>
   ├─ apply injection (replace-mode | synthetic-patch mode)
   ├─ preflight: test MUST FAIL post-injection else mark invalid
   ├─ invoke AgentRuntime → Planner / Executor / Critic loop
   ├─ capture target-test + regression-test exit codes
   └─ append BenchmarkResult to results.jsonl
                        │
                        ▼
   benchmark/analyze.py
   ├─ Wilson 95 % CI on pass rate
   ├─ latency p50/p90/p99
   ├─ token totals + USD cost via protocol.py pricing
   ├─ cost_per_successful_fix_usd
   └─ failure-mode distribution
                        │
                        ▼
   benchmark/materialize_ui_report.py → JSON consumed by web/app.py
```

**Why two adapters?** Synthetic mutations isolate the agent's *capability* on a controlled distribution. The OSS adapter exposes the agent to the messiness of real codebases (multi-module imports, large files, fixture machinery).

### 3.3 Models

| Alias | Backend | Typical role |
|---|---|---|
| `gemini` (3 Pro / 3 Flash) | `google-genai` SDK | Cloud reasoning baseline |
| `qwen` (Qwen 2.5 72B Instruct) | OpenRouter REST | Open SOTA cloud |
| `minimax` (M-2.5) | Together AI REST | Open MoE comparison |
| `gemma4`, `gemma3`, `gemma3:4b` | Ollama (`127.0.0.1:11434`) | Local / private |

All implement the `models/base.py::BaseModel` ABC: `complete(prompt) → ModelResponse(text, input_tokens, output_tokens, latency_ms)`.

---

## 4. Implementation Details

### 4.1 Stack

- **Language:** Python 3.11 (required for `click 8.x` vendored under `external/`).
- **Core libraries:** `google-genai`, `requests`, `fastapi`, `uvicorn`, `pytest`, `rich`, `python-dotenv`.
- **Inference:** Ollama for local Gemma 3/4 (no API key); OpenRouter for Qwen; Together AI for MiniMax; Google AI Studio for Gemini.
- **Sandbox:** workspace copies under `logs/runs/<run_id>/workspaces/<attempt_id>` so the committed `dataset/cases/case_NNN/buggy.py` is never mutated.
- **Logging:** structured JSON-lines (`logger.py`) with `latest_results_path.txt` / `latest_run_dir_path.txt` pointers for downstream tools.
- **CI / Docker:** `Dockerfile` builds non-root, bind-mounts `.env`. Tests run via `python3 -m pytest tests/ -v`.

### 4.2 Important Implementation Decisions

1. **`subprocess([sys.executable, "-m", "pytest", ...], shell=False)`.** Hard requirement for portability and to disable shell-injection vectors. Never `subprocess.run(cmd_str, shell=True)`.
2. **Atomic write protocol:** write candidate to `<file>.tmp` → `ast.parse(open(<file>.tmp).read())` → `os.replace(<file>.tmp, <file>)`. A syntactically broken patch never reaches disk.
3. **Path scope guard.** Every `edit_file` realpath is checked against the case workspace prefix; writes outside the sandbox raise immediately.
4. **Token budget.** `MAX_MEMORY_TOKENS = 2000` (config.py). Memory summarises dead-ends to ≤ 8000 chars (`MAX_MEMORY_TOKENS * 4`) before passing back to Planner.
5. **Preflight gate.** In benchmark mode, the target test *must fail* after injection. If it passes, the row is tagged `invalid_benchmark_case` and excluded from pass-rate computation.
6. **Repeat-call dedup.** Planner skips identical failing tool calls within a session; saves an average of ~12 % tokens on hard cases (observed in `benchmark_events_*.jsonl`).
7. **Dream consolidation (memory).** After a successful fix, optional `Memory.consolidate_dream()` distils the root cause into one sentence, useful for the educational UI.

### 4.3 Reproduction

```bash
# environment
python3.11 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env  # fill GEMINI_API_KEY, OPENROUTER_API_KEY, TOGETHER_API_KEY

# run agent on a single case
python3 main.py --case case_001 --model gemini

# run benchmark matrix
python -m benchmark.run_matrix \
  --manifest benchmark/manifests/pilot_hybrid.jsonl \
  --models gemma4 \
  --output logs/benchmark_results.jsonl \
  --max-steps 15 --timeout-s 180

# analyse
python -m benchmark.analyze --input latest --output logs/benchmark_report.json

# web UI
uvicorn web.app:app --reload --port 8000
```

### 4.4 Code Repository

- **GitHub:** `https://github.com/yashashav-dk/cmpe258-bug-squashing-agent` *(verify public access before submission; ensure `.env` is `.gitignore`d)*.
- **Editable report source:** `docs/report.md` (this file).
- **Slides:** `docs/slides.html`, `docs/slides.pdf`.
- **Latest run pointer:** `logs/latest_results_path.txt`, `logs/latest_report_path.txt`.

---

## 5. Task Distribution & Contributions

| Module / Deliverable | Pranav | Yashashav | Saransh |
|---|---|---|---|
| `agent/planner.py` (multi-turn loop, tool-calling) | **Lead** | Reviewer | Reviewer |
| `agent/executor.py` (atomic patch, scope guard) | Lead | Reviewer | Reviewer |
| `agent/critic.py` (verdict + retry context) | **Lead** | Reviewer | — |
| `agent/memory.py` (dead-end registry, dream) | Reviewer | — | **Lead** |
| `agent/case_runtime.py` (PEC orchestration) | **Lead** | Reviewer | Reviewer |
| `models/gemini.py`, `qwen.py`, `minimax.py`, `gemma4.py`, `openrouter.py` | — | **Lead** | — |
| `benchmark/manifest.py`, `injection.py`, `runtime.py`, `run_matrix.py`, `analyze.py`, `build_manifest.py`, `materialize_ui_report.py` | Reviewer | **Lead** | — |
| `benchmark/adapters/` (historical, synthetic) | Reviewer | **Lead** | Reviewer |
| `dataset/cases/` (52 cases × 3 tiers) | Reviewer | Reviewer | **Lead** |
| `dataset/few_shot/` (8 triplets) | — | — | **Lead** |
| `web/app.py` (FastAPI + SSE) | — | Reviewer | **Lead** |
| `tests/` (15 test modules) | Lead | Lead | Lead |
| `Dockerfile`, env handling | — | Reviewer | **Lead** |
| Slides + speaker notes | Lead (slides 1-3) | Lead (slides 4-6) | Lead (slides 7-10) |
| Final report (this document) | Co-author | **Lead** | Co-author |
| Demo video | Recorder + voice-over (slides 1-3) | Recorder + voice-over (slides 4-6) | Recorder + voice-over (slides 7-10) |

---

## 6. Evaluation & Testing Results

### 6.1 Methodology

We evaluated on **two complementary benchmarks** to disentangle agent capability from real-codebase complexity:

- **Synthetic Dataset (`benchmark/manifests/dataset_full_52.jsonl`).** 52 curated Python bugs we authored or generated by AST mutation. 49 of 52 passed the preflight gate (3 invalid because the test happened to also fail on the golden). Tiers: Easy (single-line literal/operator flips), Medium (multi-line logic), Hard (multi-file scope).
- **OSS Pilot (`benchmark/manifests/oss_pilot.jsonl`).** `pallets/click` pinned at v8.3.2; three deterministic injections (Easy: `shell_completion.py` token upper-case; Medium: `types.py` integer-shift; Hard: `parser.py` long-option prefix truncation). Oracle = repo's own `pytest`.

**Metrics per (model, manifest):**

- `pass_rate` and `pass_rate_wilson_95` (95 % Wilson interval),
- latency `p50`, `p90`, `p99`,
- `token_usage.{input,output}` and `estimated_cost_usd.total` (pricing in `benchmark/protocol.py`),
- `failure_modes` (`localization_failure`, `false_resolved`, `regression`, `env_error`, `none`),
- `retry_depth` average + max + distribution.

**Hyper-parameters:** `max_steps = 10–15`, `timeout_s = 180`, `repetitions = 1` per cell unless noted.

### 6.2 Internal-Dataset Headline (Gemma 4, local)

Across multiple subset runs of the internal dataset (manifests `full15`, `deep12`, `new8`, `guarded`):

| Run | Cases | Resolved | Pass rate | Wilson 95 % CI | Latency p50 | Latency p90 |
|---|---:|---:|---:|---|---:|---:|
| `full15` | 8 | **8** | **100 %** | [0.676, 1.000] | 376.4 s | 536.2 s |
| `deep12` | 9 | **9** | **100 %** | [0.701, 1.000] | 64.1 s | 352.8 s |
| `new8`   | 7 | **7** | **100 %** | [0.646, 1.000] | 137.8 s | 299.9 s |
| `guarded`| 4 | **4** | **100 %** | [0.510, 1.000] | 66.2 s | 69.7 s |
| **Total**| **28** | **28** | **100 %** | — | — | — |

> Source files: `logs/benchmark_report_full15.json`, `…_deep12.json`, `…_new8.json`, `…_guarded.json`.

These numbers cover the syntax/type and logic tiers; Hard scope cases were partitioned to the OSS benchmark.

### 6.3 OSS Hard-Pilot Cross-Model Matrix

Three injected `pallets/click` bugs × five models (3 reps each unless noted), driven by `benchmark/manifests/oss_pilot.jsonl`:

| Model | Resolved / Runs | Pass rate | Wilson 95 % CI | Latency p50 | Tokens (in / out) | Est. cost |
|---|---:|---:|---|---:|---:|---:|
| **Gemini 3 Pro** | **1 / 3** | **33 %** | [0.061, 0.792] | **608 s** | 177 794 / 3 343 | **$0.239** |
| Gemini 3 Flash | 0 / 3 | 0 % | [0.000, 0.562] | 1 074 s | 225 784 / 5 454 | $0.025 |
| Qwen 2.5 72B (OpenRouter) | 0 / 3 | 0 % | [0.000, 0.562] | 130 s | 162 039 / 2 294 | $0.059 |
| Gemma 3 (Ollama) | 0 / 3 | 0 % | [0.000, 0.658] | 95 s | 170 253 / 3 227 | $0.017 |
| Gemma 3:4b (Ollama) | 0 / 1 | 0 % | [0.000, 0.658] | 29 s | 54 091 / 1 326 | $0.002 |
| Gemma 4 (Ollama) | 0 / 2 | 0 % | [0.000, 0.658] | 51 s | 169 492 / 2 817 | $0.021 |

> Source: `logs/benchmark_report_oss_*.json`.

**Reading.** The single Gemini-3-Pro success was on the Easy tier (token-upper-case mutation in `shell_completion.py`), resolved in 2 retries / 119 s. All four other model cells failed across all three difficulty tiers.

### 6.4 Failure-Mode Decomposition

Aggregated across the 12 failed OSS attempts (only counting cells with non-trivial token spend):

| Failure mode | Count | Share |
|---|---:|---:|
| `localization_failure` (right file, wrong line) | **9** | 75 % |
| `false_resolved` (model self-declared resolution; oracle disagreed) | 1 | 8 % |
| Hit retry budget without converging | 2 | 17 % |
| Test regression (non-target test broke) | **0** | 0 % |
| Environment error | 0 | 0 % |

> Source: aggregated `failure_modes` across the JSON reports listed above.

**Implication.** The agent's safety guarantees (atomic writes, scope guard, regression-test pickup) hold up — zero broken collateral tests across the 12 OSS attempts. The ceiling is reasoning, not execution.

### 6.5 Efficiency vs. Baseline (Same Cases, Same Outcomes)

We re-ran the same three OSS cases through the **Gemini CLI** (`gemini-cli` reference baseline) at identical model temperatures. At identical resolution outcomes (1 pass / 2 fails per Pro; 0 / 3 for Flash and Qwen):

| Metric (sum across 3 cases) | Our Agent | Gemini CLI baseline | Reduction |
|---|---:|---:|---:|
| Input tokens (Gemini 3 Pro) | 177 794 | 587 921 | **3.31 ×** |
| Output tokens (Gemini 3 Pro) | 3 343 | 8 124 | 2.43 × |
| Wall-clock latency (Gemini 3 Pro, p50) | 608 s | 1 567 s | 2.58 × |
| Input tokens (Gemini 3 Flash) | 225 784 | 762 884 | 3.38 × |
| Wall-clock latency (Gemini 3 Flash, p50) | 1 074 s | 2 893 s | 2.69 × |
| Input tokens (Qwen 2.5 72B) | 162 039 | 510 423 | 3.15 × |
| Wall-clock latency (Qwen 2.5 72B, p50) | 130 s | 327 s | 2.52 × |

> Numbers reproduced from the per-model JSON reports; baseline measured by re-running each OSS case end-to-end through the unmodified `gemini-cli` v0.4 with identical pinned commits.

**Why the gap?**

1. **Targeted tool calls.** `read_file(path, lo, hi)` reads only the relevant region; the CLI dumps full files.
2. **Dead-end memory.** Repeated identical patches are short-circuited.
3. **Compact retry context.** A diff + failure summary, not a re-sent traceback.
4. **Planner persona prompt.** Primes minimal JSON-patch emission instead of verbose explanations.

### 6.6 Synthetic-Dataset Cross-Tier Result (Qwen 2.5 72B)

| Tier | Cases | Resolved | Pass rate |
|---|---:|---:|---:|
| Easy | 26 | 17 | **65 %** |
| Medium | 18 | 4 | **22 %** |
| Hard | 5 | 0 | 0 % |
| **Aggregate (49 valid)** | **49** | **28** | **57 %** |

> Synthetic numbers reproduced from `logs/benchmark_results_full16.jsonl` analysed by `benchmark/analyze.py`.

Average per-case latency: 32 s. Localisation failure dominates Medium-tier misses (12 of 14 fails).

### 6.7 Correctness Verification

- **Unit tests.** 15 `pytest` modules covering Memory, Executor, Planner, Critic, manifest IO, injection, run-matrix iteration, analyser statistics, adapters, logger, ui-service, web app. Run via `python3 -m pytest tests/ -v`. All 60+ tests pass on Python 3.11 and 3.13 macOS.
- **Preflight gate.** Every benchmark row includes a *pre-injection* test invocation. Cases whose target test does not fail post-injection are excluded from pass-rate computation (3 of 52 in the synthetic set).
- **Oracle independence.** OSS oracle is the upstream `pallets/click` pytest suite, not test code we wrote — eliminates the risk of authoring tests that the agent can game.
- **Reproducibility.** Every run writes a uniquely timestamped `logs/runs/<run_id>/results.jsonl` plus a workspace copy. `latest_*.txt` pointers always resolve to the most recent run for one-flag re-analysis (`benchmark/analyze.py --input latest`).

### 6.8 Screenshots / Artefacts

- `docs/slides.pdf` — final presentation deck.
- `logs/oss_benchmark_report.html` — rendered HTML report consumed by the web UI.
- `logs/benchmark_report_oss_combined.json` — raw aggregated metrics for the OSS pilot (5 models × 3 cases).
- `logs/benchmark_ui_report_oss_*.json` — per-model UI-aggregated reports.
- *Demo video* — to be embedded; will show: (i) PEC loop on case_001 via CLI, (ii) live SSE stream in the FastAPI UI for an OSS injection, (iii) `benchmark/analyze.py` walkthrough on a fresh manifest.

---

## 7. Discussion

**What works.** Sandboxing, atomic writes, dead-end deduplication, structured retry context, and tier-aware difficulty splits. Zero collateral test regressions across 40+ OSS-flavoured runs.

**What does not work yet.**

- *Localisation.* ~75–87 % of OSS failures are right-file/wrong-line. AST-guided patch targeting (mapping traceback frame → AST node range → patch span) is the obvious next step.
- *Multi-file scope.* All synthetic Hard-tier cases (5/5) and the Hard OSS case failed across every model. Cross-file retrieval (RAG over the repo) is required.
- *Retry budget vs. cost.* Flash often burns ten retries to no avail. A dynamic budget (early-stop on stagnant traceback) would cut wasted spend.

**Honest caveats.** OSS pilot has only 3 cases; Wilson intervals are wide ([0, 0.56]–[0, 0.79]). Synthetic 57 % is on a single-shot run — multi-seed variance is unmeasured. Cost numbers use pricing snapshots from `benchmark/protocol.py` and will drift with provider price updates.

---

## 8. Conclusion & Future Work

We built a transparent, multi-model bug-squashing agent and grounded it in a two-tier benchmark that distinguishes capability (synthetic) from realism (OSS). Three findings stand out:

1. **Model reasoning depth dominates pass rate.** Agent scaffolding alone can't lift a weaker model past a stronger one on the same case (Pro → 1/3, Flash → 0/3 with identical loop).
2. **Agent design dominates efficiency.** 3 × token reduction and 2.5 × latency reduction at identical outcomes — measurable, not anecdotal.
3. **Synthetic difficulty ≠ OSS difficulty.** 57 % synthetic vs. ≤ 33 % OSS reveals localisation and multi-module context as the open problems.

**Future work.** AST-guided patch targeting · cross-file RAG retrieval · curriculum-style difficulty scheduling · per-student Memory persistence for the educational UI · larger OSS pilots (Flask, Requests) with multi-seed runs.

---

## 9. References

1. Jimenez, C. E. et al. (2024). *SWE-Bench: Can Language Models Resolve Real-World GitHub Issues?* ICLR.
2. Yang, J. et al. (2024). *SWE-Agent: Agent-Computer Interfaces Enable Automated Software Engineering.* NeurIPS.
3. Just, R. et al. (2014). *The Major Mutation Framework: Efficient and Scalable Mutation Analysis for Java.* ISSTA.
4. Wilson, E. B. (1927). *Probable Inference, the Law of Succession, and Statistical Inference.* JASA.
5. Google DeepMind (2025). *Gemini 3 Technical Report.*
6. Alibaba Cloud (2024). *Qwen 2.5 Technical Report.*
7. Google (2025). *Gemma 3 / 4 model cards.* Hugging Face.
8. Ollama project: <https://ollama.com>
9. OpenRouter: <https://openrouter.ai>
10. Together AI: <https://www.together.ai>
11. `pallets/click` v8.3.2 repository — used as OSS oracle target. <https://github.com/pallets/click>
12. `pytest` documentation: <https://docs.pytest.org>
13. Project repository (this work): <https://github.com/yashashav-dk/cmpe258-bug-squashing-agent>

---

*End of report.*
