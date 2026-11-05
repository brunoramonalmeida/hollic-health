"""A test double of the browser side of the end-to-end chat: the same
protocol ``front/src/chatCrypto.js`` implements (ECDH P-256 identity keys,
ephemeral ECDH + HKDF-SHA256 + AES-256-GCM envelopes, AES-256-GCM messages
and attachments with the conversation bound as associated data)."""

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEK_INFO = b"sante-saude chat v1 kek"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def unb64(text: str) -> bytes:
    return base64.b64decode(text)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def jwk_of(public_key) -> dict:
    numbers = public_key.public_numbers()
    return {"kty": "EC", "crv": "P-256", "x": b64url(numbers.x.to_bytes(32, "big")), "y": b64url(numbers.y.to_bytes(32, "big"))}


def key_from_jwk(jwk: dict):
    return ec.EllipticCurvePublicNumbers(int.from_bytes(unb64url(jwk["x"]), "big"), int.from_bytes(unb64url(jwk["y"]), "big"), ec.SECP256R1()).public_key()


def fingerprint(jwk: dict) -> str:
    return hashlib.sha256(b"\x04" + unb64url(jwk["x"]) + unb64url(jwk["y"])).hexdigest()


def kek(private_key, peer_jwk: dict) -> bytes:
    shared = private_key.exchange(ec.ECDH(), key_from_jwk(peer_jwk))
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=KEK_INFO).derive(shared)


def message_aad(conversation_id: int) -> bytes:
    return f"sante-saude chat v1 message {conversation_id}".encode()


def attachment_aad(conversation_id: int) -> bytes:
    return f"sante-saude chat v1 attachment {conversation_id}".encode()


class Party:
    """One browser: an identity key and, once obtained, the conversation key."""

    def __init__(self):
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.jwk = jwk_of(self.private_key.public_key())
        self.fingerprint = fingerprint(self.jwk)
        self.conversation_key: bytes | None = None

    def rotate(self):
        """A new device / a reset: the old envelopes no longer open."""
        self.__init__()

    def publish(self, client, headers):
        response = client.put("/chat/key", headers=headers, json={"public_key": self.jwk})
        assert response.status_code == 200, response.text
        assert response.json()["fingerprint"] == self.fingerprint
        return response.json()

    def new_conversation_key(self) -> bytes:
        self.conversation_key = AESGCM.generate_key(bit_length=256)
        return self.conversation_key

    def wrap_for(self, recipient_jwk: dict) -> dict:
        ephemeral = ec.generate_private_key(ec.SECP256R1())
        recipient_fingerprint = fingerprint(recipient_jwk)
        iv = os.urandom(12)
        wrapped = AESGCM(kek(ephemeral, recipient_jwk)).encrypt(iv, self.conversation_key, recipient_fingerprint.encode())
        return {"recipient_fingerprint": recipient_fingerprint, "ephemeral_public_key": jwk_of(ephemeral.public_key()), "iv": b64(iv), "wrapped_key": b64(wrapped)}

    def unwrap(self, envelope: dict) -> bytes:
        self.conversation_key = AESGCM(kek(self.private_key, envelope["ephemeral_public_key"])).decrypt(
            unb64(envelope["iv"]), unb64(envelope["wrapped_key"]), envelope["recipient_fingerprint"].encode()
        )
        return self.conversation_key

    def encrypt_message(self, conversation_id: int, text: str, attachments: list | None = None) -> dict:
        iv = os.urandom(12)
        payload = json.dumps({"text": text, "attachments": attachments or []}).encode()
        return {"ciphertext": b64(AESGCM(self.conversation_key).encrypt(iv, payload, message_aad(conversation_id))), "iv": b64(iv)}

    def decrypt_message(self, conversation_id: int, message: dict) -> dict:
        payload = AESGCM(self.conversation_key).decrypt(unb64(message["iv"]), unb64(message["ciphertext"]), message_aad(conversation_id))
        return json.loads(payload)

    def encrypt_blob(self, conversation_id: int, content: bytes) -> tuple[bytes, str]:
        iv = os.urandom(12)
        return AESGCM(self.conversation_key).encrypt(iv, content, attachment_aad(conversation_id)), b64(iv)

    def decrypt_blob(self, conversation_id: int, blob: bytes, iv: str) -> bytes:
        return AESGCM(self.conversation_key).decrypt(unb64(iv), blob, attachment_aad(conversation_id))
