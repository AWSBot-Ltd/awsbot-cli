# awsbot agent Implementation

## Overview

A complete interactive coding agent has been integrated into awsbot-cli, supporting 6 different LLM providers and full configuration management.

## What Was Built

### 1. **Core Agent Module** (`awsbot_cli/commands/agent.py`)

**~1,174 lines** — Complete implementation adapted from Nanocode ch12:

#### Brain Providers (6 total)
- **Claude** — Direct Anthropic API via `anthropic` SDK
- **Bedrock** — AWS Bedrock via boto3 `bedrock-runtime.converse()`
- **OpenAI** — OpenAI API via `openai` SDK
- **Gemini** — Google Gemini via `google-genai` SDK
- **DeepSeek** — DeepSeek via Anthropic-compatible HTTP API
- **Ollama** — Local model via Anthropic-compatible HTTP API

#### Tool System (9 tools)
All copied verbatim from Nanocode ch12:
- **Plan-safe** (read-only): `ReadFile`, `ListFiles`, `SearchCodebase`, `WritePlan`, `SaveMemory`, `SearchWeb`
- **Act-only** (write/execute): `WriteFile`, `EditFile`, `RunCommand`

#### Key Features
- **Plan vs Act Mode** — `/mode act` to enable writes; default is read-only
- **Mode Switching** — `/switch` cycles through available brains
- **Memory System** — Persistent scratchpad at `.awsbot_agent/memory.md`
- **Conversation Compaction** — Auto-summarizes when approaching token limit (75% of context)
- **Interactive REPL** — Prompt-based input, `/q` to quit
- **Extensible Format Translation** — Each brain translates between its API format and internal Anthropic message format

### 2. **Configuration Subcommand** (`awsbot_cli/commands/agent_config.py`)

**~80 lines** — Manages per-profile agent settings:

```bash
awsbot-cli agent-config set --provider bedrock --model <id> --region us-east-1
awsbot-cli agent-config show
```

Integrates with existing `~/.awsbot/config.json` via `update_profile()` and `get_profile()`.

Config stored under `profiles[profile_name].agent`:
```json
{
  "provider": "bedrock",
  "model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
  "region": "us-east-1",
  "timeout": 30
}
```

### 3. **Comprehensive Tests** (`tests/test_agent.py`)

**~540 lines** — Adapted directly from `test_nanocode.py`:

- **Provider Tests** — 6 BRAINS registry checks
- **Tool Tests** — ReadFile, WriteFile, EditFile, ListFiles, SearchCodebase, RunCommand, SearchWeb
- **Agent Tests** — Mode switching, plan/act mode filtering, conversation history
- **Config Tests** — Mock-based tests for profile config load/save
- **Compaction Tests** — Token budget handling
- **FakeBrain** — Deterministic test double

### 4. **Integration with awsbot-cli**

**Updated `awsbot_cli/main.py`**:
- Added imports: `agent`, `agent_config`
- Registered both as Typer subcommands: `agent`, `agent-config`

**Updated `pyproject.toml`**:
- Added: `anthropic`, `openai`, `ddgs`
- Moved `requests` and `rich` from dev to core dependencies (they're now used at runtime)

## Usage

### Start an Agent

```bash
# Plan mode (default, read-only)
awsbot-cli agent run

# With specific provider
awsbot-cli agent run --provider bedrock --model anthropic.claude-3-5-sonnet-20241022-v2:0

# Act mode (write/execute enabled)
awsbot-cli agent run --act

# With region (for Bedrock)
awsbot-cli agent run --provider bedrock --region us-west-2
```

### Configure a Profile

```bash
# Set provider for active profile
awsbot-cli agent-config set --provider bedrock --model anthropic.claude-3-5-sonnet-20241022-v2:0 --region us-east-1

# View configuration
awsbot-cli agent-config show

# Set for specific profile
awsbot-cli agent-config set --provider claude --profile my-profile
```

### Interactive Commands

Once running:
- Type prompts normally — agent thinks and executes tools
- `/q` — quit
- `/switch` — toggle to next brain
- `/mode act` / `/mode plan` — switch safety mode

## Provider Matrix

| Provider | Env Var | Status | Notes |
|---|---|---|---|
| claude | `ANTHROPIC_API_KEY` | ✓ Direct SDK | Extended thinking enabled |
| bedrock | AWS creds | ✓ boto3 | Auto-detects region from env/config |
| openai | `OPENAI_API_KEY` | ✓ SDK | Translates OpenAI tool format to Anthropic |
| gemini | `GEMINI_API_KEY` | ✓ SDK | Already a dependency (google-genai) |
| deepseek | `DEEPSEEK_API_KEY` | ✓ HTTP | Anthropic-compatible endpoint |
| ollama | None | ✓ HTTP | Requires local Ollama running on 11434 |

## Architecture Highlights

### Message Format Adapters
Each non-Anthropic brain translates its API format to/from the internal Anthropic message format:
- **Bedrock** — `toolUse` ↔ `tool_use`, `toolResult` ↔ `tool_result`, etc.
- **OpenAI** — `tool_calls` (OpenAI) ↔ `ToolCall` objects (Anthropic)
- **Gemini** — Chat session state ↔ conversation history
- **DeepSeek/Ollama** — Use `requests` with retry logic (identical to Nanocode)

### Token Compaction
When `last_input_tokens > context_limit * 0.75`, agent:
1. Makes a secondary brain.think() call without tools
2. Receives a summarized version of conversation
3. Replaces history with synthetic user message containing summary
4. Continues with full context window available

### Configuration Priority (for agent run)
1. CLI flags (highest priority)
2. Config profile `agent` dict
3. Environment variables
4. Built-in defaults

## Files Modified / Created

| File | Lines | Status |
|---|---|---|
| `awsbot_cli/commands/agent.py` | 1,174 | ✓ Created |
| `awsbot_cli/commands/agent_config.py` | 80 | ✓ Created |
| `tests/test_agent.py` | 540 | ✓ Created |
| `awsbot_cli/main.py` | 4 lines | ✓ Modified |
| `pyproject.toml` | 5 lines | ✓ Modified |

## Dependencies Added

```toml
anthropic = ">=0.40.0,<1.0.0"
openai = ">=1.0.0,<2.0.0"
ddgs = ">=6.0.0"
requests = ">=2.32.5,<3.0.0"          # moved from dev to core
rich = ">=13.7.0,<14.0.0"             # moved from dev to core
```

## Testing

All tests use mocked LLM calls (FakeBrain), so they run without API keys:

```bash
poetry run pytest tests/test_agent.py -v
```

Individual test examples:
- `test_brains_registry_has_expected_providers()` — 6 providers present
- `test_plan_mode_hides_write_tools()` — safety enforcement
- `test_compact_conversation_summarizes()` — token budget handling
- `test_agent_config_reads_from_profile()` — config integration

## Next Steps / Future Enhancements

1. **Streaming responses** — SSE/streaming output (like appendix variant of Nanocode)
2. **Extended thinking** — Expose thinking budget as CLI flag
3. **Tool output caching** — Avoid re-running identical tools
4. **MCP integration** — Wire in the existing code indexer MCP server
5. **Web search caching** — Cache DuckDuckGo results per session
6. **Custom tools** — API for teams to register domain-specific tools

## Notes

- All 9 tools copied verbatim from Nanocode ch12 (proven stable)
- Bedrock brain handles the format translation complexity internally; from the agent's perspective it's transparent
- No breaking changes to existing awsbot-cli commands
- Agent is fully sandboxed — can be run safely by default (plan mode)
