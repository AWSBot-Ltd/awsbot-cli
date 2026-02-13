from types import SimpleNamespace


def create_args_namespace(**kwargs):
    """
    Creates a namespace object that mimics argparse,
    allowing Typer to talk to the legacy Orchestrator.
    """
    defaults = {
        "mr": False,
        "jira": False,
        "all": False,
        "s3_inventory": False,
        "profile": None,
        "ssm": None,
        "clean_amis": False,
        "env": None,
        "dry_run": False,
        "report": None,
        "share": None,
        "local": False,
        "clean_s3": False,
        "csv_file": "life_cycle_buckets.csv",
        "billing": False,
        "service": None,
        "start": None,
        "end": None,
        "log_format": "text",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)
