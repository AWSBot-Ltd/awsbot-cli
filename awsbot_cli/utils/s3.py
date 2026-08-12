import json

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


def _load_bucket_policy(s3_client, bucket_name):
    """
    Fetches and parses bucket_name's policy.
    Returns (policy_dict, had_policy, error_message); error_message is None
    on success. If no policy exists, returns a fresh empty policy skeleton.
    """
    try:
        response = s3_client.get_bucket_policy(Bucket=bucket_name)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchBucketPolicy":
            return {"Version": "2012-10-17", "Statement": []}, False, None
        return None, False, str(e)

    try:
        policy = json.loads(response["Policy"])
    except json.JSONDecodeError as e:
        return None, True, str(e)

    return policy, True, None


def has_ssl_only_statement(policy):
    """Checks a parsed bucket policy dict for an AllowSSLRequestsOnly Sid."""
    return any(
        s.get("Sid") == "AllowSSLRequestsOnly" for s in policy.get("Statement", [])
    )


def check_ssl_policy(s3_client, bucket_name):
    """
    Read-only check: does bucket_name already deny non-HTTPS requests?
    Returns one of: "COMPLIANT", "NON_COMPLIANT", "ERROR".
    """
    policy, _, error = _load_bucket_policy(s3_client, bucket_name)
    if error is not None:
        print(f"  ❌ Error fetching policy for {bucket_name}: {error}")
        return "ERROR"

    return "COMPLIANT" if has_ssl_only_statement(policy) else "NON_COMPLIANT"


def merge_ssl_policy(s3_client, bucket_name, dry_run=False):
    """
    Ensures bucket_name's policy denies non-HTTPS requests, merging into
    any existing policy without overwriting other statements.
    Returns one of: "CREATED", "MODIFIED", "SKIPPED", "ERROR".
    """
    policy, had_policy, error = _load_bucket_policy(s3_client, bucket_name)
    if error is not None:
        print(f"  ❌ Error fetching policy for {bucket_name}: {error}")
        return "ERROR"

    if has_ssl_only_statement(policy):
        print(f"  ⚠️  Skipping {bucket_name}: SSL-only statement already present.")
        return "SKIPPED"

    deny_statement = {
        "Sid": "AllowSSLRequestsOnly",
        "Effect": "Deny",
        "Principal": "*",
        "Action": "s3:*",
        "Resource": [
            f"arn:aws:s3:::{bucket_name}",
            f"arn:aws:s3:::{bucket_name}/*",
        ],
        "Condition": {"Bool": {"aws:SecureTransport": "false"}},
    }
    policy.setdefault("Statement", []).append(deny_statement)
    result = "MODIFIED" if had_policy else "CREATED"

    if dry_run:
        verb = "create" if result == "CREATED" else "modify"
        print(f"  [DRY RUN] Would {verb} SSL-only policy for {bucket_name}")
        return result

    try:
        s3_client.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(policy))
    except ClientError as e:
        print(f"  ❌ Failed to update policy for {bucket_name}: {e}")
        return "ERROR"

    return result


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
