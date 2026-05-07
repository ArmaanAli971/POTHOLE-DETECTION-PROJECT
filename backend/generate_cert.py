"""
RoadScan AI — SSL Certificate Generator
Run once: python generate_cert.py
Then set USE_HTTPS = True in app.py
"""
import datetime, ipaddress, socket

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "cryptography"])
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

try:
    local_ip = socket.gethostbyname(socket.gethostname())
except Exception:
    local_ip = "192.168.1.100"

print(f"Generating SSL certificate for IP: {local_ip} ...")

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
    x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Maharashtra"),
    x509.NameAttribute(NameOID.LOCALITY_NAME, "Pune"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "RoadScan AI"),
    x509.NameAttribute(NameOID.COMMON_NAME, local_ip),
])

cert = (
    x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.datetime.utcnow())
    .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
    .add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            x509.IPAddress(ipaddress.IPv4Address(local_ip)),
        ]),
        critical=False,
    )
    .sign(key, hashes.SHA256())
)

with open("cert.pem", "wb") as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

with open("key.pem", "wb") as f:
    f.write(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))

print(f"\n✅  cert.pem and key.pem created!")
print(f"\nNext steps:")
print(f"  1. Open app.py → set  USE_HTTPS = True")
print(f"  2. Restart backend:  python app.py")
print(f"  3. On phone, open:   https://{local_ip}:5000")
print(f"     → Tap Advanced → Proceed anyway")
print(f"  4. Then open:        https://{local_ip}:3000")
print(f"\nLive camera will now work on your phone ✅")
