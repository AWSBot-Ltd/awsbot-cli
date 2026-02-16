import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from typer.testing import CliRunner

# Update this path to match your project structure
from awsbot_cli.commands.vpn import app

runner = CliRunner()


@pytest.fixture
def mock_boto():
    with patch("awsbot_cli.commands.vpn.boto3.client") as mock_client:
        yield mock_client


# @pytest.mark.unit
# def test_list_vpns_success(mock_boto):
#     """Test the 'list' command output and logic."""
#     mock_ec2 = MagicMock()
#     mock_acm = MagicMock()
#
#     # FORCE WIDE TERMINAL: This prevents 'rich' from truncating the domain name
#     with patch("awsbot_cli.commands.vpn.console.width", 200):
#         # Configure mock returns
#         mock_boto.side_effect = lambda service, **kwargs: (
#             mock_ec2 if service == "ec2" else mock_acm
#         )
#
#         mock_ec2.describe_client_vpn_endpoints.return_value = {
#             "ClientVpnEndpoints": [
#                 {
#                     "ClientVpnEndpointId": "cvpn-12345",
#                     "Status": {"Code": "available"},
#                     "SplitTunnel": True,
#                     "ServerCertificateArn": "arn:aws:acm:server",
#                     "AuthenticationOptions": [
#                         {
#                             "Type": "certificate-authentication",
#                             "MutualAuthentication": {
#                                 "ClientRootCertificateChain": "arn:aws:acm:client"
#                             },
#                         }
#                     ],
#                 }
#             ]
#         }
#
#         # Mock ACM response for cert info
#         future_date = datetime.now(timezone.utc) + timedelta(days=40)
#         mock_acm.describe_certificate.return_value = {
#             "Certificate": {"NotAfter": future_date, "DomainName": "vpn.example.com"}
#         }
#
#         result = runner.invoke(app, ["list"])
#
#         assert result.exit_code == 0
#         assert "cvpn-12345" in result.stdout
#         assert "vpn.example.com" in result.stdout
#         assert "available" in result.stdout


@pytest.mark.unit
def test_create_cert_import_aws(mock_boto):
    """Test cert creation with the --import-aws flag."""
    mock_acm = MagicMock()
    mock_boto.return_value = mock_acm
    mock_acm.import_certificate.return_value = {
        "CertificateArn": "arn:aws:acm:new-cert"
    }

    # Mock the PKI generation and File IO
    with (
        patch("awsbot_cli.commands.vpn.generate_vpn_pki") as mock_pki,
        patch("pathlib.Path.read_bytes") as mock_read,
        patch("pathlib.Path.exists", return_value=True),
    ):
        mock_pki.return_value = (Path("ca.crt"), Path("server.crt"), Path("server.key"))
        mock_read.return_value = b"fake-data"

        result = runner.invoke(app, ["create-cert", "test.com", "--import-aws"])

        assert result.exit_code == 0
        assert "arn:aws:acm:new-cert" in result.stdout
        mock_acm.import_certificate.assert_called_once()


@pytest.mark.unit
def test_rotate_cert_server_success(mock_boto):
    """Test the certificate rotation flow for a server cert."""
    mock_ec2 = MagicMock()
    mock_acm = MagicMock()
    mock_boto.side_effect = lambda service, **kwargs: (
        mock_ec2 if service == "ec2" else mock_acm
    )

    # 1. Setup discovery mocks
    mock_ec2.describe_client_vpn_endpoints.return_value = {
        "ClientVpnEndpoints": [
            {
                "ClientVpnEndpointId": "cvpn-123",
                "ServerCertificateArn": "arn:old-cert",
                "AuthenticationOptions": [],
            }
        ]
    }
    mock_acm.describe_certificate.return_value = {
        "Certificate": {"DomainName": "vpn.com"}
    }

    # 2. Setup rotation and export mocks
    mock_ec2.export_client_vpn_client_configuration.return_value = {
        "ClientConfiguration": "remote vpn.com\n<ca>\nOLD_CA\n</ca>"
    }

    with (
        patch("awsbot_cli.commands.vpn.generate_vpn_pki") as mock_pki,
        patch("pathlib.Path.read_bytes", return_value=b"data"),
        patch("pathlib.Path.read_text", return_value="NEW_CA"),
        patch("pathlib.Path.write_text") as mock_write,
        patch("typer.confirm", return_value=True),
    ):
        mock_pki.return_value = (Path("ca"), Path("crt"), Path("key"))

        result = runner.invoke(app, ["rotate-cert", "cvpn-123", "server"])

        assert result.exit_code == 0
        assert "Rotation Complete!" in result.stdout
        # Verify ACM was updated
        mock_acm.import_certificate.assert_called_once_with(
            CertificateArn="arn:old-cert",
            Certificate=b"data",
            PrivateKey=b"data",
            CertificateChain=b"data",
        )
        # Verify file was saved
        mock_write.assert_called()


@pytest.mark.unit
def test_create_vpn_network_discovery(mock_boto):
    """Test the networking auto-discovery and CIDR selection."""
    mock_ec2 = MagicMock()
    mock_acm = MagicMock()
    mock_boto.side_effect = lambda service, **kwargs: (
        mock_ec2 if service == "ec2" else mock_acm
    )

    # Setup discovery mocks: VPC is 10.0.0.0/16
    mock_ec2.describe_vpcs.return_value = {
        "Vpcs": [{"VpcId": "vpc-1", "CidrBlock": "10.0.0.0/16"}]
    }
    mock_ec2.describe_security_groups.return_value = {
        "SecurityGroups": [{"GroupId": "sg-1"}]
    }
    mock_ec2.describe_subnets.return_value = {"Subnets": [{"SubnetId": "subnet-1"}]}
    mock_acm.import_certificate.return_value = {"CertificateArn": "arn:123"}
    mock_ec2.create_client_vpn_endpoint.return_value = {
        "ClientVpnEndpointId": "cvpn-new"
    }

    with (
        patch("awsbot_cli.commands.vpn.generate_vpn_pki") as mock_pki,
        patch("pathlib.Path.read_bytes", return_value=b"data"),
    ):
        mock_pki.return_value = (Path("ca"), Path("crt"), Path("key"))

        result = runner.invoke(app, ["create-vpn", "vpn.test.com"])

        assert result.exit_code == 0
        # Verify the code picked the first non-overlapping candidate
        _, kwargs = mock_ec2.create_client_vpn_endpoint.call_args
        assert kwargs["ClientCidrBlock"] == "10.250.0.0/22"
