import json
import logging
import os
import sys
from enum import Enum

# Define the parent logger name for the whole package
PARENT_LOGGER = "awsbot_cli"


# --- 2. Define your choices here ---
class LogFormat(str, Enum):
    text = "text"
    json = "json"


# --- Formatters ---
class JSONFormatter(logging.Formatter):
    """Outputs logs as JSON for CloudWatch/Lambda."""

    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "location": f"{record.pathname}:{record.lineno}",
            "service": os.environ.get("SERVICE_NAME"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


class HumanReadableFormatter(logging.Formatter):
    """Outputs logs as clean text for CLI users."""

    def format(self, record):
        return f"[{record.levelname}] {record.getMessage()}"


# --- Internal Helper ---
def _configure_handler(logger_instance, fmt_type):
    """Clears existing handlers and adds the correct one."""
    # Remove existing handlers to prevent duplicates
    if logger_instance.handlers:
        for h in logger_instance.handlers[:]:
            logger_instance.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    if fmt_type == "text":
        handler.setFormatter(HumanReadableFormatter())
    else:
        handler.setFormatter(JSONFormatter())

    logger_instance.addHandler(handler)
    # Prevent logs from bubbling up to the system Root logger (which might print duplicates)
    logger_instance.propagate = False


# --- Public API ---
def get_logger(name: str):
    """
    Returns a logger for the specific module.
    Ensures the PARENT logger is configured once.
    """
    # 1. Get the requested logger (e.g., awsbot_cli.lambda_functions.cleanup)
    logger = logging.getLogger(name)

    # 2. Check if the PARENT logger ('awsbot_cli') is configured.
    #    If not, configure it with defaults.
    parent_logger = logging.getLogger(PARENT_LOGGER)
    if not parent_logger.handlers:
        # Default to JSON/Env settings if not explicitly set yet
        default_fmt = os.environ.get("LOG_FORMAT", "json").lower()
        _configure_handler(parent_logger, default_fmt)
        parent_logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

    return logger


def set_log_format(fmt_type: str):
    """
    Reconfigures ONLY the parent logger.
    Child loggers will naturally bubble up to this one.
    """
    fmt_type = fmt_type.lower()
    os.environ["LOG_FORMAT"] = fmt_type

    # Only configure the parent 'awsbot_cli' logger
    parent_logger = logging.getLogger(PARENT_LOGGER)
    _configure_handler(parent_logger, fmt_type)


# --- Output Helpers (Unchanged) ---
def print_cli_table(data, headers):
    if not data:
        return
    widths = {h: len(h) for h in headers}
    for row in data:
        for h in headers:
            widths[h] = max(widths[h], len(str(row.get(h, ""))))
    fmt = "  ".join([f"{{:<{widths[h]}}}" for h in headers])
    print("-" * (sum(widths.values()) + len(headers) * 2))
    print(fmt.format(*headers))
    print("-" * (sum(widths.values()) + len(headers) * 2))
    for row in data:
        print(fmt.format(*[str(row.get(h, "")) for h in headers]))
    print("-" * (sum(widths.values()) + len(headers) * 2))


def print_formatted_output(data, headers=None):
    mode = os.environ.get("LOG_FORMAT", "json").lower()
    if mode == "text":
        if headers:
            print_cli_table(data, headers)
        else:
            print(data)
    else:
        print(json.dumps({"cli_output": data}, default=str))
