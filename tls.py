"""Self-signed TLS cert generation for iOS Safari (getUserMedia needs HTTPS)."""
from __future__ import annotations

import base64
import datetime
import ipaddress
import logging
import uuid
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

log = logging.getLogger(__name__)

ROOT = Path(__file__).parent
CERT_FILE = ROOT / 'cert.pem'
KEY_FILE = ROOT / 'key.pem'


def ensure_cert(lan_ip: str | None = None) -> tuple[Path, Path]:
    if CERT_FILE.exists() and KEY_FILE.exists():
        return CERT_FILE, KEY_FILE

    log.info("Generating self-signed cert at %s", CERT_FILE)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, 'iPhone Mic Bridge CA'),
    ])

    san_entries: list[x509.GeneralName] = [
        x509.DNSName('localhost'),
        x509.IPAddress(ipaddress.IPv4Address('127.0.0.1')),
    ]
    if lan_ip:
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(lan_ip)))
        except ValueError:
            pass

    now = datetime.datetime.now(datetime.timezone.utc)
    # Self-signed cert used both as root CA (so iOS can install it as a trust
    # anchor) and as the TLS server cert — simplest setup for a LAN tool.
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365 * 5))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    KEY_FILE.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    return CERT_FILE, KEY_FILE


def build_mobileconfig(cert_pem_path: Path) -> bytes:
    """Build an iOS .mobileconfig profile that installs the cert as a trusted root.

    After installation the user must also enable the cert under
    Settings → General → About → Certificate Trust Settings.
    """
    cert = x509.load_pem_x509_certificate(cert_pem_path.read_bytes())
    cert_der = cert.public_bytes(serialization.Encoding.DER)
    cert_b64 = base64.b64encode(cert_der).decode('ascii')

    # Chunk base64 into 52-char lines for pretty plist output.
    cert_b64_wrapped = '\n'.join(cert_b64[i:i + 52] for i in range(0, len(cert_b64), 52))

    cert_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, 'iphonemicbridge.cert')).upper()
    profile_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, 'iphonemicbridge.profile')).upper()

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>PayloadContent</key>
    <array>
        <dict>
            <key>PayloadCertificateFileName</key>
            <string>iphone-mic-bridge.cer</string>
            <key>PayloadContent</key>
            <data>
{cert_b64_wrapped}
            </data>
            <key>PayloadDescription</key>
            <string>Trust root for iPhone Mic Bridge local server</string>
            <key>PayloadDisplayName</key>
            <string>iPhone Mic Bridge CA</string>
            <key>PayloadIdentifier</key>
            <string>com.iphonemicbridge.cert.{cert_uuid}</string>
            <key>PayloadType</key>
            <string>com.apple.security.root</string>
            <key>PayloadUUID</key>
            <string>{cert_uuid}</string>
            <key>PayloadVersion</key>
            <integer>1</integer>
        </dict>
    </array>
    <key>PayloadDescription</key>
    <string>Installs the self-signed cert used by the iPhone Mic Bridge so Safari trusts WSS connections.</string>
    <key>PayloadDisplayName</key>
    <string>iPhone Mic Bridge</string>
    <key>PayloadIdentifier</key>
    <string>com.iphonemicbridge.profile.{profile_uuid}</string>
    <key>PayloadOrganization</key>
    <string>iPhone Mic Bridge</string>
    <key>PayloadRemovalDisallowed</key>
    <false/>
    <key>PayloadType</key>
    <string>Configuration</string>
    <key>PayloadUUID</key>
    <string>{profile_uuid}</string>
    <key>PayloadVersion</key>
    <integer>1</integer>
</dict>
</plist>
"""
    return plist.encode('utf-8')
