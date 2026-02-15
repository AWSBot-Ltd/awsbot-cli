from botocore.exceptions import ClientError


def append_lifecycle_rule(s3_client, bucket_name, new_rule):
    """Fetches existing rules, appends the new one (checking for ID conflict), and saves."""
    try:
        # 1. Fetch
        try:
            current_config = s3_client.get_bucket_lifecycle_configuration(
                Bucket=bucket_name
            )
            rules = current_config.get("Rules", [])
        except ClientError as e:
            if (
                e.response.get("Error", {}).get("Code")
                == "NoSuchLifecycleConfiguration"
            ):
                rules = []
            else:
                print(f"  ❌ Error fetching config for {bucket_name}: {e}")
                return False

        # 2. Check Deduplication
        rule_id = new_rule["ID"]
        for rule in rules:
            if rule.get("ID") == rule_id:
                print(f"  ⚠️  Skipping {bucket_name}: Rule '{rule_id}' already exists.")
                return True

        # 3. Append
        rules.append(new_rule)

        # 4. Save
        s3_client.put_bucket_lifecycle_configuration(
            Bucket=bucket_name, LifecycleConfiguration={"Rules": rules}
        )
        return True
    except ClientError as e:
        print(f"  ❌ Failed to update {bucket_name}: {e}")
        return False


def resolve_buckets(s3_client, bucket: str = None, filter_keyword: str = None):
    """
    Helper to resolve targets:
    1. If bucket is specified, return just that one (after verifying existence).
    2. If filter_keyword, return all matching buckets.
    3. If neither, return ALL buckets.
    """
    if bucket:
        # Check if single bucket exists
        try:
            s3_client.head_bucket(Bucket=bucket)
            return [bucket]
        except ClientError as e:
            print(f"❌ Error finding bucket '{bucket}': {e}")
            return []

    # List all buckets
    try:
        response = s3_client.list_buckets()
        all_buckets = [b["Name"] for b in response.get("Buckets", [])]
    except ClientError as e:
        print(f"❌ Error listing buckets: {e}")
        return []

    if filter_keyword:
        filtered = [b for b in all_buckets if filter_keyword in b]
        if not filtered:
            print(f"⚠️ No buckets found containing '{filter_keyword}'")
        return filtered

    return all_buckets
