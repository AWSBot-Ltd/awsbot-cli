import pytest
from types import SimpleNamespace

# Adjust the import path to match your project structure
from awsbot_cli.utils.compat import create_args_namespace


pytestmark = pytest.mark.unit


def test_create_args_namespace_defaults():
    """Verify that the function returns an object with all expected default values."""
    args = create_args_namespace()

    assert isinstance(args, SimpleNamespace)
    assert args.mr is False
    assert args.dry_run is False
    assert args.csv_file == "life_cycle_buckets.csv"
    assert args.log_format == "text"
    assert args.profile is None
    assert args.service is None


def test_create_args_namespace_overrides():
    """Verify that passing kwargs correctly overrides the default values."""
    args = create_args_namespace(
        dry_run=True, profile="production", csv_file="custom.csv"
    )

    # Overridden values
    assert args.dry_run is True
    assert args.profile == "production"
    assert args.csv_file == "custom.csv"

    # Untouched defaults
    assert args.mr is False
    assert args.log_format == "text"


def test_create_args_namespace_extra_args():
    """Verify that the function accepts and stores keys not present in the default dict."""
    args = create_args_namespace(new_feature_flag=True, region="us-east-1")

    assert args.new_feature_flag is True
    assert args.region == "us-east-1"


def test_create_args_namespace_attribute_access():
    """Verify the resulting object supports dot notation (Namespace behavior)."""
    args = create_args_namespace(env="dev")

    # Should work via dot notation
    assert args.env == "dev"

    # Should NOT be subscriptable like a dict (matches argparse behavior)
    with pytest.raises(TypeError):
        _ = args["env"]


def test_create_args_namespace_immutability_of_defaults():
    """Ensure that calling the function once doesn't pollute subsequent calls."""
    first_call = create_args_namespace(profile="first-profile")
    second_call = create_args_namespace()

    assert first_call.profile == "first-profile"
    assert second_call.profile is None  # Should have reset to default
