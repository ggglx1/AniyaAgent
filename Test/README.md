# HappyClaude Test Suite

These tests cover the deterministic agent runtime pieces before testing real LLM behavior:

- tool execution and tool-call validation
- permission and workspace safety boundaries
- task persistence and dependency flow
- background task lifecycle
- runtime conversation persistence and per-session locking
- loop guard behavior

Run with the project conda environment:

```powershell
cd C:\Users\24021\Desktop\java\learnclaudecode\HappyClaude
.\Test\run_tests.ps1
```

Run one deterministic suite only:

```powershell
.\Test\run_tests.ps1 -Suite tools
.\Test\run_tests.ps1 -Suite permissions
.\Test\run_tests.ps1 -Suite runtime
.\Test\run_tests.ps1 -Suite loop
```

The output is printed to the terminal and also saved under:

```text
Test\results\
```

The most recent run is copied to:

```text
Test\results\latest.log
```

Equivalent direct command:

```powershell
conda run -n claude python -m unittest discover -s Test -p "test_*.py"
```

## Real Agent Benchmark

Run real LLM-backed agent benchmarks with:

```powershell
cd C:\Users\24021\Desktop\java\learnclaudecode\HappyClaude
.\Test\run_agent_benchmark.ps1
```

Run one loop benchmark scenario only:

```powershell
.\Test\run_agent_benchmark.ps1 -Scenario response_speed
.\Test\run_agent_benchmark.ps1 -Scenario multi_round
.\Test\run_agent_benchmark.ps1 -Scenario long_context
.\Test\run_agent_benchmark.ps1 -Scenario loop
.\Test\run_agent_benchmark.ps1 -Scenario tools
```

Run a higher-round conversation benchmark:

```powershell
.\Test\run_multi_round_benchmark.ps1 -Rounds 30
```

For high-round runs, inspect these columns in `latest_agent_benchmark_metrics.csv`:

- `round_index`
- `duration_ms`
- `duration_delta_ms`
- `stage_setup_ms`
- `stage_llm_ms`
- `stage_wrapup_ms`
- `stored_messages_after_run`
- `conversation_chars_after_run`
- `loop_turns`
- `loop_nudges`
- `loop_stops`

Safety red-team cases such as path escape and dangerous shell commands are not run by default. Run them explicitly:

```powershell
.\Test\run_agent_benchmark.ps1 -Scenario tools -IncludeSafety
```

By default this uses:

```text
conda environment: claude
mode: real
rounds: 5
context sizes: from Test/testData/agent_benchmark_cases.json
```

You can tune the run:

```powershell
.\Test\run_agent_benchmark.ps1 -Rounds 10 -ContextSizes "0,20000,100000,200000"
```

You can skip tool behavior cases if you only want timing and loop metrics:

```powershell
.\Test\run_agent_benchmark.ps1 -SkipTools
```

Benchmark outputs are saved under:

```text
Test\results\benchmarks\
```

Most useful files:

```text
latest_agent_benchmark_report.md
latest_agent_benchmark_metrics.csv
latest_agent_benchmark_summary.csv
latest_agent_benchmark_summary.json
latest_agent_benchmark.jsonl
```

How to read them:

- `latest_agent_benchmark_report.md`: best human-readable report.
- `latest_agent_benchmark_metrics.csv`: one row per benchmark case, good for Excel.
- `latest_agent_benchmark_summary.csv`: aggregated success rate and latency by scenario.
- `latest_agent_benchmark.jsonl`: raw per-case metrics for programmatic analysis.

The real benchmark covers:

- response speed
- multi-round same-session availability
- long-context impact
- loop idle detection
- tool behavior success and failure
- model output format errors

The current default scope is the HappyClaude loop/runtime core: latency analysis, tool-call correctness, model-output format health, and sustained operation. Web/mobile bridge testing and destructive safety probes are intentionally out of the default path.

The benchmark data comes from:

```text
Test\testData\agent_benchmark_cases.json
```

For real runs, per-case details include audit-derived counters when available:

- `llm_requests`
- `tool_calls`
- `blocked_tool_calls`
- `loop_turns`
- `loop_nudges`
- `loop_stops`
- `repeated_tool_calls`
