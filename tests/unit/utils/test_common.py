import pytest

from awsbot_cli.utils.common import format_bytes


@pytest.mark.unit
@pytest.mark.parametrize(
    "input_bytes, expected_output",
    [
        (500, "500.00"),  # Fixed: removed trailing space
        (1024, "1.00 KB"),  # Passed!
        (1024**2, "1.00 MB"),  # Passed!
        (1024**3 * 1.5, "1.50 GB"),  # Passed!
        (1024**4, "1.00 TB"),  # Passed!
        (1024**5, "1.00 PB"),  # Passed! (Thanks to the loop n < 5 fix)
    ],
)
def test_format_bytes_scales(input_bytes, expected_output):
    assert format_bytes(input_bytes) == expected_output


@pytest.mark.unit
def test_format_bytes_zero():
    """Checks handling of zero bytes."""
    assert format_bytes(0) == "0.00"  # Fixed: removed trailing space
