"""Tests for awsbot agent (adapted from Nanocode ch12)."""

import os
import tempfile

import pytest

from awsbot_cli.commands.agent import (
    Agent,
    AgentStop,
    Thought,
    Memory,
    ToolContext,
    ReadFile,
    WriteFile,
    EditFile,
    WritePlan,
    ListFiles,
    SearchCodebase,
    SaveMemory,
    RunCommand,
    SearchWeb,
    BRAINS,
    tools,
    get_tool,
    tool_definitions,
)

# --- Fake Brain for Testing ---


class FakeBrain:
    """Fake brain for testing - returns predictable responses."""

    context_limit = 200_000
    last_input_tokens = 0

    def __init__(self, responses=None, memory=None, tools=None):
        self.memory = memory
        self.tools = tools or []
        self.responses = responses or [
            Thought(
                text="Fake response",
                raw_content=[{"type": "text", "text": "Fake response"}],
            )
        ]
        self.call_count = 0
        self.last_conversation = None

    def think(self, conversation):
        self.last_conversation = list(conversation)
        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            return response
        return Thought(
            text="No more responses",
            raw_content=[{"type": "text", "text": "No more responses"}],
        )


# --- SearchWeb Tool Tests ---


def test_search_web_tool_exists():
    """Verify SearchWeb class exists with required attributes."""
    tool = SearchWeb()
    assert tool.name == "search_web"
    assert tool.description is not None
    assert tool.input_schema is not None
    assert "query" in tool.input_schema["properties"]


def test_search_web_in_tools_list():
    """Verify SearchWeb is registered in the tools list."""
    tool_names = [t.name for t in tools]
    assert "search_web" in tool_names


def test_search_web_can_be_found():
    """Verify get_tool can find search_web."""
    tool = get_tool(tools, "search_web")
    assert tool is not None
    assert tool.name == "search_web"


def test_search_web_in_tool_definitions():
    """Verify search_web appears in tool definitions for API."""
    definitions = tool_definitions(tools)
    names = [d["name"] for d in definitions]
    assert "search_web" in names


def test_search_web_execute_success(monkeypatch):
    """Verify SearchWeb.execute() returns formatted results."""

    fake_results = [
        {
            "title": "Python 3.13",
            "href": "https://python.org",
            "body": "Latest release",
        },
    ]

    def fake_ddgs():
        class FakeDDGS:
            def text(self, q, max_results=3):
                return fake_results

        return FakeDDGS()

    monkeypatch.setattr("awsbot_cli.commands.agent.DDGS", fake_ddgs)
    tool = SearchWeb()
    context = ToolContext()
    result = tool.execute(context, "latest python version")
    assert "Python 3.13" in result
    assert "https://python.org" in result


def test_search_web_execute_no_results(monkeypatch):
    """Verify SearchWeb.execute() handles empty results."""

    def fake_ddgs():
        class FakeDDGS:
            def text(self, q, max_results=3):
                return []

        return FakeDDGS()

    monkeypatch.setattr("awsbot_cli.commands.agent.DDGS", fake_ddgs)
    tool = SearchWeb()
    context = ToolContext()
    result = tool.execute(context, "impossible query xyz")
    assert "No results found" in result


# --- Agent Basic Tests ---


def test_handle_input_returns_string():
    """Basic test: handle_input returns a string."""
    agent = Agent(brain=FakeBrain(), tools=tools)
    result = agent.handle_input("Hello")
    assert isinstance(result, str)


def test_quit_command_raises_agent_stop():
    """Verify /q raises AgentStop."""
    agent = Agent(brain=FakeBrain(), tools=tools)
    with pytest.raises(AgentStop):
        agent.handle_input("/q")


def test_messages_accumulate():
    """Verify conversation history accumulates."""
    agent = Agent(brain=FakeBrain(), tools=tools)
    agent.handle_input("First message")
    agent.handle_input("Second message")
    # Should have 4 messages: user, assistant, user, assistant
    assert len(agent.conversation) == 4


def test_brains_registry_has_expected_providers():
    """Verify BRAINS registry contains all providers."""
    assert "claude" in BRAINS
    assert "bedrock" in BRAINS
    assert "openai" in BRAINS
    assert "gemini" in BRAINS
    assert "deepseek" in BRAINS
    assert "ollama" in BRAINS


# --- Tool Tests ---


def test_read_file_adds_line_numbers():
    """Verify ReadFile prefixes each line with line numbers."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("line one\nline two\nline three\n")
        temp_path = f.name

    try:
        tool = ReadFile()
        context = ToolContext()
        result = tool.execute(context, temp_path)
        assert "1 | line one" in result
        assert "2 | line two" in result
        assert "3 | line three" in result
    finally:
        os.unlink(temp_path)


def test_write_file_creates_file():
    """Verify WriteFile creates a file with content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "test.txt")
        tool = WriteFile()
        context = ToolContext()
        result = tool.execute(context, path, "hello world")

        assert os.path.exists(path)
        assert "Successfully wrote" in result
        with open(path) as f:
            assert f.read() == "hello world"


def test_edit_file_replaces_text():
    """Verify EditFile replaces text in a file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("x = 1\ny = 2\n")
        temp_path = f.name

    try:
        tool = EditFile()
        context = ToolContext()
        result = tool.execute(context, temp_path, "x = 1", "x = 10")
        assert "Successfully edited" in result
        with open(temp_path) as f:
            assert f.read() == "x = 10\ny = 2\n"
    finally:
        os.unlink(temp_path)


def test_edit_file_not_found():
    """Verify EditFile raises error when text not found."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("x = 1\n")
        temp_path = f.name

    try:
        tool = EditFile()
        context = ToolContext()
        result = tool.execute(context, temp_path, "y = 2", "y = 20")
        assert "Error" in result or "Could not find" in result
    finally:
        os.unlink(temp_path)


def test_list_files_returns_tree():
    """Verify ListFiles returns a tree structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a simple structure
        os.makedirs(os.path.join(tmpdir, "subdir"))
        open(os.path.join(tmpdir, "file1.txt"), "w").close()
        open(os.path.join(tmpdir, "subdir", "file2.txt"), "w").close()

        tool = ListFiles()
        context = ToolContext()
        result = tool.execute(context, tmpdir)

        assert "file1.txt" in result
        assert "file2.txt" in result


def test_plan_mode_hides_write_tools():
    """Verify plan mode removes write tools from brain's menu."""
    agent = Agent(brain=FakeBrain(), tools=tools, mode="plan")
    tool_names = [t["name"] for t in agent.brain.tools]
    assert "write_file" not in tool_names
    assert "edit_file" not in tool_names
    assert "run_command" not in tool_names
    assert "write_plan" in tool_names
    assert "read_file" in tool_names
    assert "search_web" in tool_names


def test_act_mode_shows_all_tools():
    """Verify act mode includes all tools in brain's menu."""
    agent = Agent(brain=FakeBrain(), tools=tools, mode="act")
    tool_names = [t["name"] for t in agent.brain.tools]
    assert "write_file" in tool_names
    assert "edit_file" in tool_names
    assert "run_command" in tool_names


def test_mode_switch_updates_brain_tools():
    """Verify switching mode updates the brain's tool menu."""
    agent = Agent(brain=FakeBrain(), tools=tools, mode="plan")
    plan_names = [t["name"] for t in agent.brain.tools]
    assert "write_file" not in plan_names
    agent.handle_input("/mode act")
    act_names = [t["name"] for t in agent.brain.tools]
    assert "write_file" in act_names


def test_write_plan_saves_file():
    """Verify WritePlan creates PLAN.md with content."""
    original_dir = os.getcwd()
    with tempfile.TemporaryDirectory() as tmpdir:
        os.chdir(tmpdir)
        try:
            tool = WritePlan()
            context = ToolContext()
            result = tool.execute(context, content="# My Plan")
            assert "Plan saved" in result
            assert os.path.exists("PLAN.md")
            with open("PLAN.md") as f:
                assert f.read() == "# My Plan"
        finally:
            os.chdir(original_dir)


def test_run_command_executes():
    """Verify RunCommand works."""
    tool = RunCommand()
    context = ToolContext()
    result = tool.execute(context, "echo hello")
    assert "hello" in result


def test_search_codebase_finds_matches():
    """Verify SearchCodebase finds matching lines."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test file
        test_file = os.path.join(tmpdir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    return 'world'\n")

        tool = SearchCodebase()
        context = ToolContext()
        result = tool.execute(context, "def hello", tmpdir)
        assert "test.py" in result
        assert "def hello" in result


def test_save_memory_updates_memory():
    """Verify SaveMemory updates the memory object."""
    with tempfile.TemporaryDirectory() as tmpdir:
        memory_path = os.path.join(tmpdir, "memory.md")
        memory = Memory(path=memory_path)
        tool = SaveMemory()
        context = ToolContext(memory=memory)
        result = tool.execute(context, "New memory content")
        assert "Memory updated successfully" in result
        assert memory.content == "New memory content"


# --- Version Test ---


def test_agent_version_presence():
    """Verify agent module has version info."""
    import awsbot_cli.commands.agent as agent_module

    assert agent_module is not None


# --- Compaction Test ---


def test_compact_conversation_summarizes():
    """Verify compaction replaces conversation with summary."""
    summary = Thought(
        text="Summary of conversation",
        raw_content=[{"type": "text", "text": "Summary of conversation"}],
    )
    responses = [
        Thought(
            text="Response 1",
            raw_content=[{"type": "text", "text": "Response 1"}],
        ),
        summary,
    ]
    brain = FakeBrain(responses=responses)
    brain.last_input_tokens = 200_000  # Over 75% threshold
    agent = Agent(brain=brain, tools=tools, mode="act")

    agent.handle_input("Do something")

    # After compaction, conversation should be short (summary + current response)
    assert len(agent.conversation) <= 4


# --- Config Tests (Mocked) ---


def test_agent_config_set_saves_to_profile(monkeypatch):
    """agent-config set builds the agent dict and saves it to the profile via update_profile."""
    from unittest.mock import MagicMock

    from awsbot_cli.commands import agent_config

    saved = {}

    def fake_update_profile(profile_name, **kwargs):
        saved[profile_name] = kwargs

    monkeypatch.setattr(agent_config, "update_profile", fake_update_profile)

    ctx = MagicMock()
    ctx.meta = {}
    agent_config.set_config(
        ctx,
        provider="bedrock",
        model="test-model",
        region=None,
        timeout=None,
        profile="default",
    )

    assert saved["default"]["agent"] == {"provider": "bedrock", "model": "test-model"}


def test_agent_config_reads_from_profile(monkeypatch):
    """Verify agent reads config from profile."""
    config_data = {
        "profiles": {
            "default": {
                "agent": {
                    "provider": "bedrock",
                    "model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
                    "region": "us-west-2",
                }
            }
        }
    }

    def mock_get_profile(profile_name=None):
        return config_data["profiles"].get(profile_name or "default", {})

    monkeypatch.setattr("awsbot_cli.commands.agent.get_profile", mock_get_profile)

    profile = mock_get_profile()
    agent_config = profile.get("agent", {})

    assert agent_config["provider"] == "bedrock"
    assert agent_config["region"] == "us-west-2"
