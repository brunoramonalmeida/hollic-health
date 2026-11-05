"""The test PKI: a root, an intermediate and person certificates shaped
like ICP-Brasil (CPF in the otherName), generated once per session. The
root is written to the trust bundle the application reads
(``ICP_BRASIL_TRUST_BUNDLE``, pinned by conftest)."""

import os
from datetime import datetime, timezone
from pathlib import Path

from app.demo_pki import DemoCA, issue_person, make_intermediate, make_root, pkcs12_bytes

PASSWORD = "certificado"
CPF = "12345678909"
_state: dict = {}


def pki() -> dict:
    """root, intermediate (trusted) and a stranger root (not trusted)."""
    if not _state:
        root = make_root("AC Raiz teste")
        intermediate = make_intermediate(root, "AC teste de Pessoas")
        stranger = make_root("AC Raiz desconhecida")
        Path(os.environ["ICP_BRASIL_TRUST_BUNDLE"]).write_bytes(root.pem())
        _state.update(root=root, intermediate=intermediate, stranger=stranger)
    return _state


def certificate(name="Lorem Ipsum", cpf=CPF, password=PASSWORD, issuer: DemoCA | None = None, **validity) -> bytes:
    """A PKCS#12 (.pfx) for a person, issued by the trusted intermediate unless told otherwise."""
    state = pki()
    issuer = issuer or state["intermediate"]
    key, cert = issue_person(issuer, name, cpf, **validity)
    chain = [issuer.certificate] if issuer is state["intermediate"] else []
    return pkcs12_bytes(key, cert, chain, password)


def expired_certificate(name="Lorem Ipsum", cpf=CPF) -> bytes:
    return certificate(name, cpf, not_before=datetime(2015, 1, 1, tzinfo=timezone.utc), not_after=datetime(2018, 1, 1, tzinfo=timezone.utc))


def untrusted_certificate(name="Lorem Ipsum", cpf=CPF) -> bytes:
    return certificate(name, cpf, issuer=pki()["stranger"])
