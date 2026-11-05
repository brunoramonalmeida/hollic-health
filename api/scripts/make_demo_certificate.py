"""A demo PKI to try the signing of prescriptions without an ICP-Brasil certificate.

Run from the api directory:

    .venv/bin/python scripts/make_demo_certificate.py

Writes to ``demo-ca/``:

* ``roots.pem`` — the demo root CA: point ``ICP_BRASIL_TRUST_BUNDLE`` at it
  (``ICP_BRASIL_TRUST_BUNDLE=./demo-ca/roots.pem`` in ``.env``);
* ``lorem-ipsum.pfx`` — an A1-like certificate for the seeded professional
  Lorem Ipsum (CPF 123.456.789-09), password ``saude123``;
* ``dolor-sit.pfx`` — the same for Dolor Sit (CPF 987.654.321-00).

Nothing here is ICP-Brasil: the certificates are trusted only by a server
whose bundle holds this demo root. In production put the real ICP-Brasil
roots in the bundle and let the professionals use their own certificates.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.demo_pki import issue_person, make_intermediate, make_root, pkcs12_bytes

PASSWORD = "saude123"
PEOPLE = (("lorem-ipsum", "Lorem Ipsum", "12345678909"), ("dolor-sit", "Dolor Sit", "98765432100"))


def main():
    folder = Path(__file__).resolve().parents[1] / "demo-ca"
    folder.mkdir(exist_ok=True)
    root = make_root("AC Raiz demo Sante Saude")
    intermediate = make_intermediate(root, "AC demo de Pessoas")
    (folder / "roots.pem").write_bytes(root.pem())
    for slug, name, cpf in PEOPLE:
        key, certificate = issue_person(intermediate, name, cpf)
        (folder / f"{slug}.pfx").write_bytes(pkcs12_bytes(key, certificate, [intermediate.certificate], PASSWORD, slug))
    print(f"Demo PKI written to {folder}:")
    print("  roots.pem           -> ICP_BRASIL_TRUST_BUNDLE=./demo-ca/roots.pem")
    for slug, name, cpf in PEOPLE:
        print(f"  {slug}.pfx  -> {name} (CPF {cpf}), password {PASSWORD}")


if __name__ == "__main__":
    main()
