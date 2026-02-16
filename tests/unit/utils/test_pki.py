import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from awsbot_cli.utils.pki import generate_vpn_pki


@pytest.fixture
def temp_pki_dir(tmp_path):
    """Provides a temporary directory for PKI file generation."""
    return tmp_path / "pki"


def test_generate_vpn_pki_creates_files(temp_pki_dir):
    """Verify that all three required files are created in the output directory."""
    domain = "test.local"
    ca_p, cert_p, key_p = generate_vpn_pki(domain, temp_pki_dir)

    assert ca_p.exists()
    assert cert_p.exists()
    assert key_p.exists()
    assert ca_p.suffix == ".crt"
    assert key_p.suffix == ".key"


def test_ca_certificate_properties(temp_pki_dir):
    """Verify the Root CA has the correct Common Name and CA extensions."""
    domain = "infra.example.com"
    ca_path, _, _ = generate_vpn_pki(domain, temp_pki_dir)

    # Load the generated CA cert
    ca_cert = x509.load_pem_x509_certificate(ca_path.read_bytes())

    # Check Common Name
    common_names = ca_cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    assert common_names[0].value == f"ca.{domain}"

    # Check Basic Constraints (Must be a CA)
    basic_constraints = ca_cert.extensions.get_extension_for_class(
        x509.BasicConstraints
    ).value
    assert basic_constraints.ca is True

    # Check Key Usage (Must have keyCertSign)
    key_usage = ca_cert.extensions.get_extension_for_class(x509.KeyUsage).value
    assert key_usage.key_cert_sign is True


def test_client_certificate_properties(temp_pki_dir):
    """Verify the Client cert is signed by the CA and has Client Auth usage."""
    domain = "vpn.internal"
    ca_path, cert_path, _ = generate_vpn_pki(domain, temp_pki_dir)

    ca_cert = x509.load_pem_x509_certificate(ca_path.read_bytes())
    client_cert = x509.load_pem_x509_certificate(cert_path.read_bytes())

    # Check that Client Issuer matches CA Subject
    assert client_cert.issuer == ca_cert.subject

    # Check Extended Key Usage (Must have Client Auth)
    eku = client_cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH in eku

    # Check Basic Constraints (Must NOT be a CA)
    basic_constraints = client_cert.extensions.get_extension_for_class(
        x509.BasicConstraints
    ).value
    assert basic_constraints.ca is False


def test_private_key_format(temp_pki_dir):
    """Verify the private key is valid and unencrypted PEM."""
    domain = "secure.com"
    _, _, key_path = generate_vpn_pki(domain, temp_pki_dir)

    key_bytes = key_path.read_bytes()

    # Attempt to load the key (will raise exception if invalid)
    key = serialization.load_pem_private_key(key_bytes, password=None)

    assert key.key_size == 2048
    assert b"BEGIN RSA PRIVATE KEY" in key_bytes
