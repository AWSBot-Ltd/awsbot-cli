import ipaddress
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory

import boto3
import jinja2
import typer
from rich.console import Console
from rich.table import Table

from awsbot_cli.utils.pki import generate_vpn_pki

BASE_DIR = Path(__file__).parent.parent.resolve()

app = typer.Typer(help="Manage VPN Certificates and Configurations")
console = Console()


class CertType(str, Enum):
    server = "server"
    client = "client"


def get_cert_info(acm_client, cert_arn: str):
    """Helper to fetch expiration and domain from ACM."""
    if not cert_arn:
        return "N/A", "N/A"
    try:
        cert = acm_client.describe_certificate(CertificateArn=cert_arn)["Certificate"]
        expiry = cert.get("NotAfter")
        domain = cert.get("DomainName", "N/A")

        days_left = (expiry - datetime.now(timezone.utc)).days
        expiry_str = expiry.strftime("%Y-%m-%d")

        if days_left < 7:
            return f"[bold red]{expiry_str}[/bold red]", domain
        elif days_left < 30:
            return f"[bold yellow]{expiry_str}[/bold yellow]", domain
        return f"[green]{expiry_str}[/green]", domain
    except Exception:
        return "[red]Error/Missing[/red]", "N/A"


@app.command(name="list")
def list_vpns():
    """Lists all Client VPN endpoints with certificate status."""
    ec2 = boto3.client("ec2")
    acm = boto3.client("acm")

    with console.status("[bold green]Fetching VPN data..."):
        response = ec2.describe_client_vpn_endpoints()
        endpoints = response.get("ClientVpnEndpoints", [])

    table = Table(title="AWS Client VPN Summary", show_lines=True)
    table.add_column("VPN ID", style="cyan")
    table.add_column("State", style="bold")
    table.add_column("Cert Type", style="magenta")
    table.add_column("Domain / ID")
    table.add_column("Expires", justify="right")
    table.add_column("Split-Tunnel", justify="center")

    for vpn in endpoints:
        vpn_id = vpn["ClientVpnEndpointId"]
        state = vpn["Status"]["Code"]
        split = "Yes" if vpn.get("SplitTunnel") else "No"

        # Server Cert
        server_arn = vpn.get("ServerCertificateArn")
        s_expiry, s_domain = get_cert_info(acm, server_arn)
        table.add_row(vpn_id, state, "Server", s_domain, s_expiry, split)

        # Client Cert (Mutual Auth)
        for auth in vpn.get("AuthenticationOptions", []):
            if auth["Type"] == "certificate-authentication":
                client_arn = auth.get("MutualAuthentication", {}).get(
                    "ClientRootCertificateChain"
                )
                c_expiry, c_domain = get_cert_info(acm, client_arn)
                table.add_row("", "", "Client", c_domain, c_expiry, "")

    console.print(table)


@app.command(name="rotate-cert")
def rotate_cert(
    vpn_id: str = typer.Argument(..., help="The Client VPN Endpoint ID"),
    cert_type: CertType = typer.Argument(
        CertType.server, help="Which certificate to rotate (server or client)"
    ),
    output_path: Path = typer.Option(
        Path.cwd(), "--output-path", help="Where to save the new VPN config file"
    ),
):
    """
    Auto-discovers the ARN and Domain from the VPN ID, rotates the cert,
    and generates a new .ovpn config.
    """
    ec2 = boto3.client("ec2")
    acm = boto3.client("acm")

    with console.status(
        f"[bold green]Discovering {cert_type.value} certificate for {vpn_id}..."
    ):
        try:
            vpn_resp = ec2.describe_client_vpn_endpoints(ClientVpnEndpointIds=[vpn_id])
            vpn = vpn_resp["ClientVpnEndpoints"][0]

            # Identify the target ARN
            target_arn = None
            if cert_type == CertType.server:
                target_arn = vpn.get("ServerCertificateArn")
            else:
                for auth in vpn.get("AuthenticationOptions", []):
                    if auth["Type"] == "certificate-authentication":
                        target_arn = auth.get("MutualAuthentication", {}).get(
                            "ClientRootCertificateChain"
                        )
                        break

            if not target_arn:
                console.print(
                    f"[red]Error: Could not find a {cert_type.value} certificate for this VPN.[/red]"
                )
                raise typer.Exit(1)

            # Identify the Domain Name from ACM
            cert_data = acm.describe_certificate(CertificateArn=target_arn)[
                "Certificate"
            ]
            domain = cert_data.get("DomainName", "server")

        except Exception as e:
            console.print(f"[red]Discovery failed: {str(e)}[/red]")
            raise typer.Exit(1)

    console.print(f"[bold blue]Target VPN:[/bold blue] {vpn_id}")
    console.print(
        f"[bold blue]Rotating:[/bold blue]   {cert_type.value.upper()} certificate"
    )
    console.print(f"[bold blue]ARN:[/bold blue]        {target_arn}")
    console.print(f"[bold blue]Domain:[/bold blue]     {domain}")

    if not typer.confirm(
        "\nThis will generate new keys and re-import them to ACM. Continue?"
    ):
        raise typer.Abort()

    with TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # 1. Generate PKI
        with console.status("[bold green]Generating new self-signed certificates..."):
            ca_crt_path, cert_path, key_path = generate_vpn_pki(domain, tmp_path)

        # 2. Re-import to ACM (using the same ARN replaces the cert content)
        with console.status("[bold green]Re-importing to ACM..."):
            acm.import_certificate(
                CertificateArn=target_arn,
                Certificate=cert_path.read_bytes(),
                PrivateKey=key_path.read_bytes(),
                CertificateChain=ca_crt_path.read_bytes(),
            )

        # 3. Export and Patch Config
        with console.status("[bold green]Generating updated .ovpn configuration..."):
            response = ec2.export_client_vpn_client_configuration(
                ClientVpnEndpointId=vpn_id
            )
            vpn_config = response["ClientConfiguration"]

            # Patch the <ca> block
            ca_content = ca_crt_path.read_text().strip()
            new_ca_block = f"<ca>\n{ca_content}\n</ca>"
            if "<ca>" in vpn_config:
                vpn_config = re.sub(
                    r"<ca>.*?</ca>", new_ca_block, vpn_config, flags=re.DOTALL
                )
            else:
                vpn_config += f"\n{new_ca_block}"

            final_file = output_path / f"{domain}-{cert_type.value}.ovpn"
            final_file.write_text(vpn_config)

    console.print("\n[bold green]✓ Rotation Complete![/bold green]")
    console.print(f"New configuration saved to: [bold]{final_file}[/bold]")


@app.command(name="create-cert")
def create_cert(
    domain: str = typer.Argument(..., help="The domain name for the certificate"),
    dest: Path = typer.Option(
        Path.cwd(), "--dest", "-d", help="Directory to save the files"
    ),
    import_aws: bool = typer.Option(
        False, "--import-aws", help="Automatically import the certificate to AWS ACM"
    ),
):
    """
    Create a new self-signed certificate and optionally import it to AWS ACM.
    """
    if not dest.exists():
        dest.mkdir(parents=True)

    acm = boto3.client("acm")
    arn_display = "N/A"

    # Fix: Wrap the whole sequence in one status or sequence them separately
    try:
        with console.status(f"[bold green]Processing certificate for {domain}..."):
            # 1. Generate Files locally
            ca_path, cert_path, key_path = generate_vpn_pki(domain, dest)

            # 2. Optional: Import to ACM
            if import_aws:
                # We update the status text instead of nesting a new status
                console.log(f"[blue]Local files generated at {dest}")
                response = acm.import_certificate(
                    Certificate=cert_path.read_bytes(),
                    PrivateKey=key_path.read_bytes(),
                    CertificateChain=ca_path.read_bytes(),
                )
                arn_display = response["CertificateArn"]

        # 3. Success Table (Outside the status block)
        table = Table(title=f"PKI Resource Summary: {domain}")
        table.add_column("Resource", style="magenta")
        table.add_column("Value/Path", style="cyan")

        table.add_row("Local Certificate", str(cert_path))
        table.add_row("Local Private Key", str(key_path))
        table.add_row("AWS ACM ARN", arn_display)

        console.print(table)
        if import_aws:
            console.print("\n[bold green]✓ Successfully imported to ACM![/bold green]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command(name="create-vpn")
def create_vpn(
    domain: str = typer.Argument(..., help="Domain for the VPN (e.g. vpn.example.com)"),
    vpc_id: str = typer.Option(
        None, "--vpc", help="VPC ID (auto-discovered if omitted)"
    ),
    security_group_id: str = typer.Option(
        None, "--sg", help="Security Group ID (uses 'default' if omitted)"
    ),
    subnet_id: str = typer.Option(
        None, "--subnet", help="Subnet ID (auto-discovered if omitted)"
    ),
    dest: Path = typer.Option(Path.cwd() / "certs", help="Local directory for certs"),
):
    """
    Bootstrap a VPN with automatic VPC, Subnet, and CIDR discovery.
    """
    ec2 = boto3.client("ec2")
    acm = boto3.client("acm")

    try:
        with console.status("[bold green]Discovering network configuration..."):
            # 1. Resolve VPC
            if not vpc_id:
                vpcs = ec2.describe_vpcs(
                    Filters=[{"Name": "is-default", "Values": ["true"]}]
                )["Vpcs"]
                if not vpcs:
                    vpcs = ec2.describe_vpcs()["Vpcs"]
                vpc_id = vpcs[0]["VpcId"]
                vpc_cidr = vpcs[0]["CidrBlock"]
                console.log(f"Using VPC: [cyan]{vpc_id}[/cyan] ({vpc_cidr})")
            else:
                vpc_cidr = ec2.describe_vpcs(VpcIds=[vpc_id])["Vpcs"][0]["CidrBlock"]

            # 2. Resolve Security Group (Default SG for the VPC)
            if not security_group_id:
                sgs = ec2.describe_security_groups(
                    Filters=[
                        {"Name": "vpc-id", "Values": [vpc_id]},
                        {"Name": "group-name", "Values": ["default"]},
                    ]
                )["SecurityGroups"]
                security_group_id = sgs[0]["GroupId"]
                console.log(f"Using Security Group: [cyan]{security_group_id}[/cyan]")

            # 3. Resolve Subnet
            if not subnet_id:
                subnets = ec2.describe_subnets(
                    Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
                )["Subnets"]
                subnet_id = subnets[0]["SubnetId"]
                console.log(f"Using Subnet: [cyan]{subnet_id}[/cyan]")

            # 4. Calculate Non-clashing Client CIDR
            # We pick a common private range that doesn't overlap with the VPC CIDR
            vpc_net = ipaddress.ip_network(vpc_cidr)
            candidates = ["10.250.0.0/22", "172.16.250.0/22", "192.168.250.0/22"]
            client_cidr = next(
                c for c in candidates if not ipaddress.ip_network(c).overlaps(vpc_net)
            )
            console.log(f"Allocated Client CIDR: [yellow]{client_cidr}[/yellow]")

        # --- Proceed with Creation ---
        with console.status("[bold green]Creating PKI and Endpoint..."):
            # Generate and Import Certs
            ca_p, cert_p, key_p = generate_vpn_pki(domain, dest)
            cert_arn = acm.import_certificate(
                Certificate=cert_p.read_bytes(),
                PrivateKey=key_p.read_bytes(),
                CertificateChain=ca_p.read_bytes(),
            )["CertificateArn"]

            # Create Endpoint
            vpn_id = ec2.create_client_vpn_endpoint(
                ClientCidrBlock=client_cidr,
                ServerCertificateArn=cert_arn,
                AuthenticationOptions=[
                    {
                        "Type": "certificate-authentication",
                        "MutualAuthentication": {
                            "ClientRootCertificateChainArn": cert_arn
                        },
                    }
                ],
                ConnectionLogOptions={"Enabled": False},
                VpcId=vpc_id,
                SecurityGroupIds=[security_group_id],
                SplitTunnel=True,
                # Adding the TagSpecifications here
                TagSpecifications=[
                    {
                        "ResourceType": "client-vpn-endpoint",
                        "Tags": [
                            {
                                "Key": "Name",
                                "Value": domain,  # Using the domain for the Name tag
                            },
                            {"Key": "ManagedBy", "Value": "AWSBOT-CLI"},
                        ],
                    }
                ],
            )["ClientVpnEndpointId"]

            # Associate Target Network
            ec2.associate_client_vpn_target_network(
                ClientVpnEndpointId=vpn_id, SubnetId=subnet_id
            )

            # Auto-authorize traffic to the VPC
            ec2.authorize_client_vpn_ingress(
                ClientVpnEndpointId=vpn_id,
                TargetNetworkCidr=vpc_cidr,
                AuthorizeAllGroups=True,
                Description="Default VPC access",
            )

        console.print(
            "\n[bold green]✓ VPN fully provisioned and authorized![/bold green]"
        )
        console.print(f"VPN ID: [bold cyan]{vpn_id}[/bold cyan]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command(name="generate-config")
def generate_config(
    domain: str = typer.Argument(..., help="The domain name used for the VPN"),
    # Point to the existing certs directory in your project root
    certs_dir: Path = typer.Option(
        BASE_DIR.parent / "certs", help="Directory where certs are stored"
    ),
    # Point to the actual location shown in your tree output: awsbot_cli/templates/
    template_path: Path = typer.Option(
        BASE_DIR / "templates" / "vpn_config.ovpn.j2",
        help="Path to your Jinja2 template",
        exists=True,
    ),
):
    """
    Generate a finalized .ovpn file from a Jinja2 template using local certs.
    """
    ec2 = boto3.client("ec2")

    try:
        with console.status(f"[bold green]Generating config for {domain}..."):
            # 1. Fetch VPN Endpoint details from AWS
            endpoints = ec2.describe_client_vpn_endpoints(
                Filters=[{"Name": "tag:Name", "Values": [domain]}]
            )["ClientVpnEndpoints"]

            if not endpoints:
                raise Exception(f"No VPN endpoint found with Name tag '{domain}'")

            endpoint = endpoints[0]
            # AWS requires random. prefix for many clients to resolve wildcard DNS
            dns_name = (
                f"random.{endpoint['DnsName'][2:]}"
                if endpoint["DnsName"].startswith("*")
                else endpoint["DnsName"]
            )

            ca_content = (certs_dir / f"{domain}-ca.crt").read_text().strip()
            cert_content = (certs_dir / f"{domain}-client.crt").read_text().strip()
            key_content = (certs_dir / f"{domain}-client.key").read_text().strip()

            # 3. Render the Template
            template = jinja2.Template(template_path.read_text())
            rendered_ovpn = template.render(
                remote_host=dns_name,
                vpn_port=443,
                ca_cert=ca_content,
                client_cert=cert_content,
                client_key=key_content,
            )

            # 4. Save to the directory where you are currently standing
            output_file = Path.cwd() / f"{domain}.ovpn"
            output_file.write_text(rendered_ovpn)

        console.print(
            "\n[bold green]✓ Configuration generated successfully![/bold green]"
        )
        console.print(f"Output file: [cyan]{output_file.name}[/cyan]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
