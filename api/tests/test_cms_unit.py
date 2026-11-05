"""Unit tests of the CMS / CAdES module: DER primitives, a signature built
and verified without the API, and what the verifier refuses."""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app import cms
from app.demo_pki import issue_person, make_intermediate, make_root

NOW = datetime(2026, 10, 22, 13, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def pki():
    root = make_root("AC Raiz unit")
    intermediate = make_intermediate(root, "AC unit")
    key, cert = issue_person(intermediate, "Lorem Ipsum", "12345678909")
    return {"root": root, "intermediate": intermediate, "key": key, "cert": cert}


def test_der_primitives_round_trip():
    for oid in ("1.2.840.113549.1.7.2", "2.16.76.1.3.1", "2.16.840.1.101.3.4.2.1", "0.9.2342.19200300.100.1.25"):
        node, _ = cms.parse_one(cms.der_oid(oid))
        assert cms.read_oid(node) == oid
    for value in (0, 1, 127, 128, 255, 256, 2**63, -1, -129):
        node, _ = cms.parse_one(cms.der_integer(value))
        assert cms.read_integer(node) == value
    long = cms.tlv(cms.TAG_OCTET_STRING, b"x" * 300)
    node, end = cms.parse_one(long)
    assert node.body == b"x" * 300 and end == len(long)
    # SET OF members are sorted by encoding (DER), whatever the order given.
    assert cms.der_set(cms.der_integer(2), cms.der_integer(1)) == cms.der_set(cms.der_integer(1), cms.der_integer(2))
    stamp = cms.read_time(cms.parse_one(cms.der_utctime(NOW))[0])
    assert stamp == NOW


def test_truncated_or_malformed_der_is_an_error():
    with pytest.raises(cms.DerError):
        cms.parse_one(b"\x30\x05\x02")
    with pytest.raises(cms.DerError):
        cms.parse_one(b"\x30\x85\x01\x01\x01\x01\x01")
    report = cms.verify_signed_data(b"garbage", b"x", [])
    assert report.valid is False and report.checks[0].name == "structure"


def test_signature_built_here_verifies_here(pki):
    content = b"%PDF-1.4 prescription"
    signature = cms.build_signed_data(content, pki["key"], pki["cert"], [pki["intermediate"].certificate], NOW)
    report = cms.verify_signed_data(signature, content, [pki["root"].certificate], NOW)
    assert report.valid is True, report.failed()
    assert report.signing_time == NOW
    assert report.digest_hex == hashlib.sha256(content).hexdigest()
    assert [cms.common_name(item.subject) for item in report.chain] == ["Lorem Ipsum:12345678909", "AC unit", "AC Raiz unit"]
    assert cms.cpf_of(pki["cert"]) == "12345678909"
    assert cms.icp_brasil_person(pki["cert"]).startswith("0101198012345678909")


def test_policy_attribute_is_carried(pki):
    content = b"x"
    policy = cms.SignaturePolicy("2.16.76.1.7.1.1.2.3", hashlib.sha256(b"policy").digest(), "http://politicas.icpbrasil.gov.br/PA_AD_RB_v2_3.der")
    signature = cms.build_signed_data(content, pki["key"], pki["cert"], [pki["intermediate"].certificate], NOW, policy)
    report = cms.verify_signed_data(signature, content, [pki["root"].certificate], NOW)
    assert report.valid is True and report.policy_oid == "2.16.76.1.7.1.1.2.3"


def test_what_the_verifier_refuses(pki):
    content = b"x"
    signature = cms.build_signed_data(content, pki["key"], pki["cert"], [pki["intermediate"].certificate], NOW)
    roots = [pki["root"].certificate]
    assert "message-digest: the file does not match the signed digest" in cms.verify_signed_data(signature, b"y", roots, NOW).failed()
    assert cms.verify_signed_data(signature, content, [], NOW).failed() == ["chain: no trusted root configured"]
    other = make_root("Other root")
    assert any(item.startswith("chain: no trusted issuer") for item in cms.verify_signed_data(signature, content, [other.certificate], NOW).failed())
    # A bit flipped in the signature value.
    broken = bytearray(signature)
    broken[-1] ^= 0x01
    assert "signature: the signature does not verify with the signer's key" in cms.verify_signed_data(bytes(broken), content, roots, NOW).failed()
    # Without the chain embedded the intermediate is unknown.
    alone = cms.build_signed_data(content, pki["key"], pki["cert"], [], NOW)
    assert not cms.verify_signed_data(alone, content, roots, NOW).valid
    assert cms.verify_signed_data(alone, content, roots + [pki["intermediate"].certificate], NOW).valid  # unless the verifier knows it
    # A signing time in the future, or outside the certificate's validity.
    future = cms.build_signed_data(content, pki["key"], pki["cert"], [pki["intermediate"].certificate], NOW + timedelta(days=1))
    assert any(item.startswith("signing-time") for item in cms.verify_signed_data(future, content, roots, NOW).failed())
    stale = cms.build_signed_data(content, pki["key"], pki["cert"], [pki["intermediate"].certificate], datetime(2019, 1, 1, tzinfo=timezone.utc))
    assert any(item.startswith("validity") for item in cms.verify_signed_data(stale, content, roots, NOW).failed())


def test_only_rsa_keys_are_supported():
    from cryptography.hazmat.primitives.asymmetric import ec

    with pytest.raises(ValueError):
        cms.build_signed_data(b"x", ec.generate_private_key(ec.SECP256R1()), None, [], NOW)
