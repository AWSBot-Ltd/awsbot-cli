#!/usr/bin/env python3
"""Quick validation script to check agent module integrity."""

import sys
import traceback


def validate():
    errors = []

    # Test 1: Import agent module
    try:
        from awsbot_cli.commands import agent

        print("✓ Agent module imports successfully")
    except Exception as e:
        errors.append(f"❌ Failed to import agent: {e}")
        traceback.print_exc()
        return errors

    # Test 2: Check BRAINS registry
    try:
        assert hasattr(agent, "BRAINS"), "BRAINS not found"
        expected = {"claude", "bedrock", "openai", "gemini", "deepseek", "ollama"}
        actual = set(agent.BRAINS.keys())
        assert actual == expected, f"Expected {expected}, got {actual}"
        print(f"✓ BRAINS registry complete: {', '.join(sorted(agent.BRAINS.keys()))}")
    except Exception as e:
        errors.append(f"❌ BRAINS registry check failed: {e}")

    # Test 3: Check Tool classes
    try:
        tool_names = {
            "ReadFile",
            "WriteFile",
            "EditFile",
            "WritePlan",
            "ListFiles",
            "SearchCodebase",
            "SaveMemory",
            "RunCommand",
            "SearchWeb",
        }
        for tool_name in tool_names:
            assert hasattr(agent, tool_name), f"Tool {tool_name} not found"
        print(f"✓ All {len(tool_names)} tool classes present")
    except Exception as e:
        errors.append(f"❌ Tool class check failed: {e}")

    # Test 4: Check tools list
    try:
        assert hasattr(agent, "tools"), "tools list not found"
        assert len(agent.tools) == 9, f"Expected 9 tools, got {len(agent.tools)}"
        print(f"✓ tools list has {len(agent.tools)} instances")
    except Exception as e:
        errors.append(f"❌ Tools list check failed: {e}")

    # Test 5: Check Agent class
    try:
        assert hasattr(agent, "Agent"), "Agent class not found"
        print("✓ Agent class present")
    except Exception as e:
        errors.append(f"❌ Agent class check failed: {e}")

    # Test 6: Import agent_config
    try:
        from awsbot_cli.commands import agent_config

        assert hasattr(agent_config, "app"), "agent_config.app not found"
        print("✓ agent_config module and app present")
    except Exception as e:
        errors.append(f"❌ Failed to import agent_config: {e}")
        traceback.print_exc()

    # Test 7: Check main.py imports
    try:
        from awsbot_cli import main

        assert hasattr(main, "app"), "main.app not found"
        print("✓ main module loads successfully")
    except Exception as e:
        errors.append(f"❌ Failed to validate main.py: {e}")
        traceback.print_exc()

    return errors


if __name__ == "__main__":
    errors = validate()

    if errors:
        print("\n" + "=" * 60)
        print("VALIDATION ERRORS:")
        for error in errors:
            print(error)
        sys.exit(1)
    else:
        print("\n" + "=" * 60)
        print("✅ All validation checks passed!")
        sys.exit(0)
