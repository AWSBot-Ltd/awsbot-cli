def list_instances(ec2_client) -> list[dict]:
    """Return one dict per EC2 instance: ID, Name, Type, State, AZ, IPs, Launch Time,
    OS, IMDSv2 enforcement, IAM instance profile, and attached security groups.
    Also carries an internal "_sg_ids" list for the caller to resolve
    internet-exposure against, stripped before rendering/export.
    """
    instances = []
    paginator = ec2_client.get_paginator("describe_instances")
    for page in paginator.paginate():
        for reservation in page.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                launch_time = inst.get("LaunchTime")
                iam_profile = inst.get("IamInstanceProfile")
                security_groups = inst.get("SecurityGroups", [])
                instances.append(
                    {
                        "Instance ID": inst.get("InstanceId"),
                        "Name": tags.get("Name", "-"),
                        "Type": inst.get("InstanceType"),
                        "State": inst.get("State", {}).get("Name"),
                        "AZ": inst.get("Placement", {}).get("AvailabilityZone"),
                        "Private IP": inst.get("PrivateIpAddress", "-"),
                        "Public IP": inst.get("PublicIpAddress", "-"),
                        "Launch Time": (
                            launch_time.strftime("%Y-%m-%d %H:%M:%S")
                            if hasattr(launch_time, "strftime")
                            else str(launch_time)
                        ),
                        "OS": inst.get("PlatformDetails", "-"),
                        "IMDSv2 Enforced": (
                            "Yes"
                            if inst.get("MetadataOptions", {}).get("HttpTokens")
                            == "required"
                            else "No"
                        ),
                        "IAM Instance Profile": (
                            iam_profile["Arn"].rsplit("/", 1)[-1]
                            if iam_profile
                            else "-"
                        ),
                        "Security Groups": (
                            ", ".join(sg["GroupName"] for sg in security_groups) or "-"
                        ),
                        "_sg_ids": [sg["GroupId"] for sg in security_groups],
                    }
                )
    return instances


def security_groups_open_to_internet(
    ec2_client, group_ids: list[str]
) -> dict[str, bool]:
    """Return {group_id: bool} for whether each group allows inbound 0.0.0.0/0 or ::/0."""
    unique_ids = sorted(set(group_ids))
    if not unique_ids:
        return {}

    result = {}
    paginator = ec2_client.get_paginator("describe_security_groups")
    # ponytail: DescribeSecurityGroups has no documented GroupIds cap; 200 is an
    # arbitrary-but-safe chunk size, bump if AWS ever rejects a batch this size.
    for i in range(0, len(unique_ids), 200):
        batch = unique_ids[i : i + 200]
        for page in paginator.paginate(GroupIds=batch):
            for sg in page.get("SecurityGroups", []):
                open_to_internet = any(
                    r.get("CidrIp") == "0.0.0.0/0" or r.get("CidrIpv6") == "::/0"
                    for perm in sg.get("IpPermissions", [])
                    for r in perm.get("IpRanges", []) + perm.get("Ipv6Ranges", [])
                )
                result[sg["GroupId"]] = open_to_internet
    return result


def ssm_enrichment(ssm_client, instance_ids: list[str]) -> dict[str, dict]:
    """Return {instance_id: {PlatformName, PlatformVersion, PatchComplianceStatus}}
    for instances managed by SSM. Instance ids not managed by SSM are absent from
    the result. Raises botocore.exceptions.ClientError on AWS failures (e.g.
    missing ssm:Describe* permissions) -- callers must treat this as a
    soft/degradable failure, not a fatal one.
    """
    unique_ids = set(instance_ids)
    if not unique_ids:
        return {}

    managed = {}
    info_paginator = ssm_client.get_paginator("describe_instance_information")
    for page in info_paginator.paginate():
        for info in page.get("InstanceInformationList", []):
            if info["InstanceId"] in unique_ids:
                managed[info["InstanceId"]] = {
                    "PlatformName": info.get("PlatformName"),
                    "PlatformVersion": info.get("PlatformVersion"),
                }

    managed_ids = sorted(managed)
    patch_paginator = ssm_client.get_paginator("describe_instance_patch_states")
    # DescribeInstancePatchStates caps InstanceIds at 50 per call.
    for i in range(0, len(managed_ids), 50):
        batch = managed_ids[i : i + 50]
        for page in patch_paginator.paginate(InstanceIds=batch):
            for state in page.get("InstancePatchStates", []):
                managed[state["InstanceId"]]["PatchComplianceStatus"] = state.get(
                    "PatchComplianceStatus", "UNKNOWN"
                )

    return managed
