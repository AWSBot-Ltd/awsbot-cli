def format_bytes(size):
    """Converts bytes to human-readable format (e.g., 1.5 GB)."""
    power = 2**10
    n = 0
    power_labels = {0: "", 1: "KB", 2: "MB", 3: "GB", 4: "TB"}

    # We allow n to reach 5 so that .get(5, 'PB') kicks in
    while size >= power and n < 5:
        size /= power
        n += 1

    return f"{size:.2f} {power_labels.get(n, 'PB')}".strip()
