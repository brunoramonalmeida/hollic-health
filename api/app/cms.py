"""CMS / CAdES detached signatures (build and verify), no external parser.

The prescriptions are signed with a **CAdES-BES detached signature**
(RFC 5652 *Cryptographic Message Syntax* + ETSI EN 319 122-1 / RFC 5126):
a ``SignedData`` structure with SHA-256, RSA PKCS#1 v1.5, the signer's
certificate and its chain embedded, and the signed attributes

* ``content-type`` (id-data), ``message-digest`` (SHA-256 of the file),
* ``signing-time``,
* ``signing-certificate-v2`` (ESS, RFC 5035: the hash of the signer's
  certificate, so the certificate cannot be swapped),
* optionally ``signature-policy-identifier`` (RFC 5126) with the ICP-Brasil
  policy (AD-RB), when the policy is configured (see app.prescriptions).

This is the ``.p7s`` format the ICP-Brasil validators (ITI's *Verificador
de Conformidade*) and OpenSSL understand, so a pharmacy can check a
prescription with the original file and its ``.p7s`` outside this system.

``build_signed_data`` encodes the structure with a small DER encoder;
``verify_signed_data`` parses it back with a small DER reader and checks
everything independently of how it was produced — the system never trusts
a signature it cannot verify itself: message digest, RSA signature over
the signed attributes, ESS certificate hash, the chain up to a trusted
root, the validity of every certificate at the signing time. Revocation
(CRL/OCSP) is not checked.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding

# --- object identifiers ----------------------------------------------------------

OID_SIGNED_DATA = "1.2.840.113549.1.7.2"
OID_DATA = "1.2.840.113549.1.7.1"
OID_CONTENT_TYPE = "1.2.840.113549.1.9.3"
OID_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
OID_SIGNING_TIME = "1.2.840.113549.1.9.5"
OID_SIGNING_CERTIFICATE_V2 = "1.2.840.113549.1.9.16.2.47"
OID_SIGNATURE_POLICY = "1.2.840.113549.1.9.16.2.15"
OID_POLICY_URI_QUALIFIER = "1.2.840.113549.1.9.16.5.1"
OID_SHA256 = "2.16.840.1.101.3.4.2.1"
OID_RSA = "1.2.840.113549.1.1.1"
OID_SHA256_RSA = "1.2.840.113549.1.1.11"
# ICP-Brasil: the person's data (birth date + CPF + NIS + RG) in the subjectAltName otherName.
OID_ICP_BRASIL_PERSON = "2.16.76.1.3.1"

# --- DER encoding -------------------------------------------------------------------

TAG_INTEGER = 0x02
TAG_OCTET_STRING = 0x04
TAG_NULL = 0x05
TAG_OID = 0x06
TAG_UTF8 = 0x0C
TAG_PRINTABLE = 0x13
TAG_IA5 = 0x16
TAG_UTCTIME = 0x17
TAG_GENERALIZEDTIME = 0x18
TAG_SEQUENCE = 0x30
TAG_SET = 0x31
TAG_CTX0 = 0xA0
TAG_CTX1 = 0xA1


def _length(size: int) -> bytes:
    if size < 0x80:
        return bytes([size])
    body = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _length(len(body)) + body


def der_sequence(*items: bytes) -> bytes:
    return tlv(TAG_SEQUENCE, b"".join(items))


def der_set(*items: bytes) -> bytes:
    """DER: the members of a SET OF are sorted by their encoding."""
    return tlv(TAG_SET, b"".join(sorted(items)))


def der_integer(value: int) -> bytes:
    size = (value.bit_length() // 8) + 1  # one extra bit keeps positives positive
    return tlv(TAG_INTEGER, value.to_bytes(size, "big", signed=True))


def der_oid(text: str) -> bytes:
    parts = [int(item) for item in text.split(".")]
    body = bytearray([40 * parts[0] + parts[1]])
    for part in parts[2:]:
        chunk = [part & 0x7F]
        part >>= 7
        while part:
            chunk.append(0x80 | (part & 0x7F))
            part >>= 7
        body.extend(reversed(chunk))
    return tlv(TAG_OID, bytes(body))


def der_octets(value: bytes) -> bytes:
    return tlv(TAG_OCTET_STRING, value)


def der_null() -> bytes:
    return tlv(TAG_NULL, b"")


def der_utctime(moment: datetime) -> bytes:
    return tlv(TAG_UTCTIME, moment.astimezone(timezone.utc).strftime("%y%m%d%H%M%SZ").encode())


def der_ia5(text: str) -> bytes:
    return tlv(TAG_IA5, text.encode("ascii"))


def algorithm_sha256() -> bytes:
    return der_sequence(der_oid(OID_SHA256), der_null())


def algorithm_rsa() -> bytes:
    return der_sequence(der_oid(OID_RSA), der_null())


def attribute(oid: str, value: bytes) -> bytes:
    return der_sequence(der_oid(oid), der_set(value))


# --- DER reading ----------------------------------------------------------------------


@dataclass
class Node:
    tag: int
    body: bytes
    raw: bytes  # the whole TLV

    @property
    def constructed(self) -> bool:
        return bool(self.tag & 0x20)

    @property
    def children(self) -> list["Node"]:
        return parse_all(self.body)


class DerError(ValueError):
    pass


def parse_one(data: bytes, offset: int = 0) -> tuple[Node, int]:
    if offset >= len(data):
        raise DerError("Unexpected end of data")
    tag = data[offset]
    if tag & 0x1F == 0x1F:
        raise DerError("Multi-byte tags are not supported")
    cursor = offset + 1
    if cursor >= len(data):
        raise DerError("Truncated length")
    first = data[cursor]
    cursor += 1
    if first < 0x80:
        size = first
    else:
        count = first & 0x7F
        if count == 0 or count > 4 or cursor + count > len(data):
            raise DerError("Invalid length")
        size = int.from_bytes(data[cursor : cursor + count], "big")
        cursor += count
    end = cursor + size
    if end > len(data):
        raise DerError("Truncated value")
    return Node(tag, data[cursor:end], data[offset:end]), end


def parse_all(data: bytes) -> list[Node]:
    nodes, offset = [], 0
    while offset < len(data):
        node, offset = parse_one(data, offset)
        nodes.append(node)
    return nodes


def read_oid(node: Node) -> str:
    if node.tag != TAG_OID:
        raise DerError("Expected an OBJECT IDENTIFIER")
    body = node.body
    first = body[0]
    parts = [str(first // 40 if first < 80 else 2), str(first % 40 if first < 80 else first - 80)]
    value = 0
    for byte in body[1:]:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(str(value))
            value = 0
    return ".".join(parts)


def read_integer(node: Node) -> int:
    if node.tag != TAG_INTEGER:
        raise DerError("Expected an INTEGER")
    return int.from_bytes(node.body, "big", signed=True)


def read_time(node: Node) -> datetime:
    text = node.body.decode("ascii")
    if node.tag == TAG_UTCTIME:
        value = datetime.strptime(text, "%y%m%d%H%M%SZ")
    elif node.tag == TAG_GENERALIZEDTIME:
        value = datetime.strptime(text[:14] + "Z", "%Y%m%d%H%M%SZ")
    else:
        raise DerError("Expected a time")
    return value.replace(tzinfo=timezone.utc)


# --- building --------------------------------------------------------------------------


@dataclass(frozen=True)
class SignaturePolicy:
    """The signature policy referenced by the signature (RFC 5126 §5.8.1):
    ICP-Brasil's AD-RB policies are identified by their OID, the SHA-256 of
    the policy file and its URL."""

    oid: str
    sha256: bytes
    url: str


def signed_attributes(
    content: bytes, certificate: x509.Certificate, signing_time: datetime, policy: SignaturePolicy | None = None
) -> bytes:
    """The members of the ``SET OF Attribute`` (sorted, DER), without the
    SET header: what is signed is ``tlv(TAG_SET, members)``; the SignerInfo
    carries the same members under the ``[0] IMPLICIT`` tag."""
    items = [
        attribute(OID_CONTENT_TYPE, der_oid(OID_DATA)),
        attribute(OID_SIGNING_TIME, der_utctime(signing_time)),
        attribute(OID_MESSAGE_DIGEST, der_octets(hashlib.sha256(content).digest())),
        # SigningCertificateV2 { certs SEQUENCE OF ESSCertIDv2 { certHash } } (hash algorithm: the default SHA-256)
        attribute(
            OID_SIGNING_CERTIFICATE_V2,
            der_sequence(der_sequence(der_sequence(der_octets(hashlib.sha256(certificate.public_bytes(Encoding.DER)).digest())))),
        ),
    ]
    if policy is not None:
        items.append(
            attribute(
                OID_SIGNATURE_POLICY,
                der_sequence(
                    der_oid(policy.oid),
                    der_sequence(algorithm_sha256(), der_octets(policy.sha256)),
                    der_sequence(der_sequence(der_oid(OID_POLICY_URI_QUALIFIER), der_ia5(policy.url))),
                ),
            )
        )
    return b"".join(sorted(items))


def build_signed_data(
    content: bytes,
    private_key: rsa.RSAPrivateKey,
    certificate: x509.Certificate,
    chain: list[x509.Certificate],
    signing_time: datetime,
    policy: SignaturePolicy | None = None,
) -> bytes:
    """A detached CAdES-BES signature (DER ``ContentInfo``) over ``content``."""
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise ValueError("Only RSA keys are supported")
    members = signed_attributes(content, certificate, signing_time, policy)
    signature = private_key.sign(tlv(TAG_SET, members), padding.PKCS1v15(), hashes.SHA256())
    signer_info = der_sequence(
        der_integer(1),
        der_sequence(certificate.issuer.public_bytes(), der_integer(certificate.serial_number)),
        algorithm_sha256(),
        tlv(TAG_CTX0, members),  # signedAttrs [0] IMPLICIT SET OF Attribute
        algorithm_rsa(),
        der_octets(signature),
    )
    certificates = [certificate] + [item for item in chain if item != certificate]
    signed_data = der_sequence(
        der_integer(1),
        der_set(algorithm_sha256()),
        der_sequence(der_oid(OID_DATA)),  # detached: no eContent
        tlv(TAG_CTX0, b"".join(item.public_bytes(Encoding.DER) for item in certificates)),
        der_set(signer_info),
    )
    return der_sequence(der_oid(OID_SIGNED_DATA), tlv(TAG_CTX0, signed_data))


# --- verifying ----------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    valid: bool = False
    checks: list[Check] = field(default_factory=list)
    signer: x509.Certificate | None = None
    chain: list[x509.Certificate] = field(default_factory=list)  # signer first, root last
    signing_time: datetime | None = None
    digest_hex: str = ""
    policy_oid: str = ""
    algorithm: str = ""

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(name, ok, detail))
        return ok

    def failed(self) -> list[str]:
        return [f"{check.name}: {check.detail}" for check in self.checks if not check.ok]


def icp_brasil_person(certificate: x509.Certificate) -> str:
    """The text of the ICP-Brasil person otherName (birth date + CPF + ...), or ''."""
    try:
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return ""
    for item in san.get_values_for_type(x509.OtherName):
        if item.type_id.dotted_string == OID_ICP_BRASIL_PERSON:
            try:
                node, _ = parse_one(item.value)
                return node.body.decode("ascii", errors="replace")
            except (DerError, IndexError):
                return ""
    return ""


def cpf_of(certificate: x509.Certificate) -> str:
    """The CPF (11 digits) of an ICP-Brasil person certificate, or ''."""
    text = icp_brasil_person(certificate)
    digits = text[8:19]
    return digits if len(digits) == 11 and digits.isdigit() else ""


def common_name(name: x509.Name) -> str:
    values = name.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    return str(values[0].value) if values else name.rfc4514_string()


def _find_children(node: Node, tag: int) -> list[Node]:
    return [child for child in node.children if child.tag == tag]


def build_chain(
    signer: x509.Certificate, pool: list[x509.Certificate], roots: list[x509.Certificate]
) -> tuple[list[x509.Certificate], bool, str]:
    """Walk from the signer up to a trusted root, verifying every link.
    Returns (chain, trusted, reason)."""
    root_der = {item.public_bytes(Encoding.DER) for item in roots}
    chain = [signer]
    current = signer
    for _ in range(8):
        if current.public_bytes(Encoding.DER) in root_der:
            return chain, True, ""
        candidates = [item for item in roots + pool if item.subject == current.issuer and item != current]
        parent = None
        for candidate in candidates:
            try:
                current.verify_directly_issued_by(candidate)
            except (InvalidSignature, ValueError, TypeError):
                continue
            parent = candidate
            break
        if parent is None:
            return chain, False, f"no trusted issuer found for '{common_name(current.subject)}' (issuer '{common_name(current.issuer)}')"
        chain.append(parent)
        current = parent
    return chain, False, "certificate chain too long"


def verify_signed_data(
    signature_der: bytes,
    content: bytes,
    roots: list[x509.Certificate],
    now: datetime | None = None,
) -> Report:
    """Verify a detached CMS signature over ``content``. Every check is
    reported; ``valid`` is true only when all of them pass."""
    report = Report()
    try:
        content_info, _ = parse_one(signature_der)
        outer = content_info.children
        if read_oid(outer[0]) != OID_SIGNED_DATA:
            report.add("structure", False, "not a CMS SignedData")
            return report
        signed_data = outer[1].children[0]
        members = signed_data.children
        version, digest_algorithms, encap = members[0], members[1], members[2]
        certificates = _find_children(signed_data, TAG_CTX0)
        signer_infos = [item for item in members[3:] if item.tag == TAG_SET][-1]
        encap_members = encap.children
        if read_oid(encap_members[0]) != OID_DATA:
            report.add("structure", False, "the signed content is not id-data")
            return report
        if len(encap_members) > 1:  # attached content: must be the very file
            embedded = encap_members[1].children[0].body
            if embedded != content:
                report.add("structure", False, "the embedded content differs from the file")
                return report
        pool = []
        for node in certificates[0].children if certificates else []:
            pool.append(x509.load_der_x509_certificate(node.raw))
        infos = signer_infos.children
        if len(infos) != 1:
            report.add("structure", False, f"expected one signer, found {len(infos)}")
            return report
        info = infos[0].children
        sid = info[1]
        digest_algorithm = read_oid(info[2].children[0])
        signed_attrs = info[3] if info[3].tag == TAG_CTX0 else None
        if signed_attrs is None:
            report.add("structure", False, "no signed attributes")
            return report
        signature_algorithm = read_oid(info[4].children[0])
        signature = info[5].body
        report.add("structure", True, f"CMS SignedData v{read_integer(version)}, {len(pool)} certificate(s) embedded")
    except (DerError, IndexError, ValueError) as exc:
        report.add("structure", False, f"malformed signature: {exc}")
        return report

    # The signer's certificate: issuer + serial number.
    signer = None
    if sid.tag == TAG_SEQUENCE:
        issuer_der, serial = sid.children[0].raw, read_integer(sid.children[1])
        signer = next((item for item in pool if item.issuer.public_bytes() == issuer_der and item.serial_number == serial), None)
    if not report.add("signer-certificate", signer is not None, "" if signer else "the signer's certificate is not embedded"):
        return report
    report.signer = signer

    # Algorithms.
    if not report.add("algorithms", digest_algorithm == OID_SHA256 and signature_algorithm in (OID_RSA, OID_SHA256_RSA), f"digest {digest_algorithm}, signature {signature_algorithm}"):
        return report
    report.algorithm = "SHA-256 with RSA (PKCS#1 v1.5)"

    # Signed attributes.
    attrs: dict[str, Node] = {}
    for node in signed_attrs.children:
        attrs[read_oid(node.children[0])] = node.children[1].children[0]
    digest = hashlib.sha256(content).digest()
    report.digest_hex = digest.hex()
    report.add("content-type", OID_CONTENT_TYPE in attrs and read_oid(attrs[OID_CONTENT_TYPE]) == OID_DATA, "content-type attribute")
    report.add(
        "message-digest",
        OID_MESSAGE_DIGEST in attrs and attrs[OID_MESSAGE_DIGEST].body == digest,
        "the file matches the signed digest" if OID_MESSAGE_DIGEST in attrs and attrs[OID_MESSAGE_DIGEST].body == digest else "the file does not match the signed digest",
    )
    if OID_SIGNING_TIME in attrs:
        try:
            report.signing_time = read_time(attrs[OID_SIGNING_TIME])
        except (DerError, ValueError):
            pass
    reference = now or datetime.now(timezone.utc)
    if report.signing_time is None:
        report.add("signing-time", False, "missing or malformed")
    elif report.signing_time > reference + timedelta(minutes=5):
        report.add("signing-time", False, f"{report.signing_time.isoformat()} is in the future")
    else:
        report.add("signing-time", True, report.signing_time.isoformat())
    ess_ok = False
    if OID_SIGNING_CERTIFICATE_V2 in attrs:
        try:
            ess = attrs[OID_SIGNING_CERTIFICATE_V2].children[0].children[0].children
            hash_node = next(item for item in ess if item.tag == TAG_OCTET_STRING)
            ess_ok = hash_node.body == hashlib.sha256(signer.public_bytes(Encoding.DER)).digest()
        except (DerError, IndexError, StopIteration):
            ess_ok = False
    report.add("signing-certificate-v2", ess_ok, "the signed certificate reference matches the signer" if ess_ok else "missing or does not match the signer's certificate")
    if OID_SIGNATURE_POLICY in attrs:
        try:
            report.policy_oid = read_oid(attrs[OID_SIGNATURE_POLICY].children[0])
        except (DerError, IndexError):
            report.policy_oid = ""

    # The signature itself: RSA over the DER SET OF signed attributes.
    public_key = signer.public_key()
    if isinstance(public_key, rsa.RSAPublicKey):
        try:
            public_key.verify(signature, tlv(TAG_SET, signed_attrs.body), padding.PKCS1v15(), hashes.SHA256())
            report.add("signature", True, f"RSA-{public_key.key_size} signature verified")
        except InvalidSignature:
            report.add("signature", False, "the signature does not verify with the signer's key")
    else:
        report.add("signature", False, "unsupported key type")

    # Chain and validity at the signing time.
    chain, trusted, reason = build_chain(signer, pool, roots)
    report.chain = chain
    report.add(
        "chain",
        trusted,
        " > ".join(common_name(item.subject) for item in chain) if trusted else (reason if roots else "no trusted root configured"),
    )
    moment = report.signing_time or now or datetime.now(timezone.utc)
    for item in chain:
        not_before = item.not_valid_before_utc
        not_after = item.not_valid_after_utc
        inside = not_before <= moment <= not_after
        report.add("validity", inside, f"'{common_name(item.subject)}' valid {not_before:%Y-%m-%d} to {not_after:%Y-%m-%d}" + ("" if inside else f", not at {moment:%Y-%m-%d %H:%M}"))
        if not inside:
            break
    report.valid = all(check.ok for check in report.checks)
    return report
