"""A throw-away PKI shaped like ICP-Brasil, for tests and local trials.

Real prescriptions are signed with certificates issued by ICP-Brasil
(the root bundle published by ITI goes in ``ICP_BRASIL_TRUST_BUNDLE``).
This module builds the same shape — a root CA, an intermediate CA and a
person certificate carrying the CPF in the ICP-Brasil ``otherName``
(OID 2.16.76.1.3.1: birth date, CPF, NIS, RG) — so the signing and the
verification can be exercised without a real certificate. Nothing here is
trusted unless its root is put in the trust bundle.
"""

from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .cms import OID_ICP_BRASIL_PERSON, TAG_PRINTABLE, tlv

KEY_SIZE = 2048


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)


def _name(common_name: str, organization: str = "ICP-Brasil (demo)") -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )


def person_other_name(cpf: str, birth_date: str = "01011980", nis: str = "", rg: str = "") -> x509.OtherName:
    """The ICP-Brasil person data: DDMMYYYY + CPF (11) + NIS (11) + RG (15)."""
    text = f"{birth_date:<8}{cpf:<11}{nis:<11}{rg:<15}".replace(" ", "0")
    return x509.OtherName(x509.ObjectIdentifier(OID_ICP_BRASIL_PERSON), tlv(TAG_PRINTABLE, text.encode("ascii")))


class DemoCA:
    def __init__(self, common_name: str, key: rsa.RSAPrivateKey, certificate: x509.Certificate):
        self.common_name = common_name
        self.key = key
        self.certificate = certificate

    def pem(self) -> bytes:
        return self.certificate.public_bytes(serialization.Encoding.PEM)


def make_root(common_name: str = "AC Raiz demo", not_before: datetime | None = None, not_after: datetime | None = None) -> DemoCA:
    key = _key()
    not_before = not_before or datetime(2020, 1, 1, tzinfo=timezone.utc)
    not_after = not_after or datetime(2045, 1, 1, tzinfo=timezone.utc)
    name = _name(common_name)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return DemoCA(common_name, key, certificate)


def make_intermediate(root: DemoCA, common_name: str = "AC demo de Pessoas") -> DemoCA:
    key = _key()
    certificate = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(root.certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(root.certificate.not_valid_before_utc)
        .not_valid_after(root.certificate.not_valid_after_utc - timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root.key.public_key()), critical=False)
        .sign(root.key, hashes.SHA256())
    )
    return DemoCA(common_name, key, certificate)


def issue_person(
    issuer: DemoCA,
    name: str,
    cpf: str | None,
    not_before: datetime | None = None,
    not_after: datetime | None = None,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """A person certificate (A1-like): CPF in the ICP-Brasil otherName when given."""
    key = _key()
    not_before = not_before or datetime(2020, 6, 1, tzinfo=timezone.utc)
    not_after = not_after or datetime(2040, 6, 1, tzinfo=timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(f"{name}:{cpf}" if cpf else name, "ICP-Brasil (demo) - pessoa fisica"))
        .issuer_name(issuer.certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(True, True, False, False, False, False, False, False, False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer.key.public_key()), critical=False)
    )
    if cpf:
        builder = builder.add_extension(x509.SubjectAlternativeName([person_other_name(cpf)]), critical=False)
    return key, builder.sign(issuer.key, hashes.SHA256())


def pkcs12_bytes(key: rsa.RSAPrivateKey, certificate: x509.Certificate, chain: list[x509.Certificate], password: str, name: str = "demo") -> bytes:
    """An A1-like ``.pfx``: the key, the certificate and the chain, protected by the password."""
    return pkcs12.serialize_key_and_certificates(
        name.encode(), key, certificate, chain, serialization.BestAvailableEncryption(password.encode())
    )
