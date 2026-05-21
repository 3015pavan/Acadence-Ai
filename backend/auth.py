import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Iterable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .database import get_db
from .tenant_context import clear_current_user, get_current_user_role, set_current_user


AUTH_SECRET = os.getenv("AUTH_SECRET") or os.getenv("JWT_SECRET") or "acadence-dev-secret-change-me"
ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("ACCESS_TOKEN_TTL_SECONDS", "1800"))
REFRESH_TOKEN_TTL_SECONDS = int(os.getenv("REFRESH_TOKEN_TTL_SECONDS", "1209600"))
PASSWORD_HASH_ITERATIONS = int(os.getenv("PASSWORD_HASH_ITERATIONS", "310000"))
PASSWORD_SALT_BYTES = int(os.getenv("PASSWORD_SALT_BYTES", "16"))
JWT_ALGORITHM = "HS256"
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(slots=True)
class AuthenticatedUser:
    id: int
    email: str
    role: str
    display_name: str
    is_active: bool = True
    tenant_key: str | None = None


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(token: str) -> bytes:
    padding = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + padding)


def _sign(message: bytes) -> bytes:
    return hmac.new(AUTH_SECRET.encode("utf-8"), message, hashlib.sha256).digest()


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password is required")
    salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
    derived_key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return "pbkdf2_sha256${iterations}${salt}${hash}".format(
        iterations=PASSWORD_HASH_ITERATIONS,
        salt=_b64url_encode(salt),
        hash=_b64url_encode(derived_key),
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_value, digest_value = stored_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = _b64url_decode(salt_value)
        expected = _b64url_decode(digest_value)
        derived_key = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iterations),
        )
        return hmac.compare_digest(derived_key, expected)
    except Exception:
        return False


def _create_token(payload: dict[str, object]) -> str:
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    header_segment = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_segment = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature_segment = _b64url_encode(_sign(signing_input))
    return f"{header_segment}.{payload_segment}.{signature_segment}"


def _decode_token(token: str) -> dict[str, object]:
    try:
        header_segment, payload_segment, signature_segment = token.split(".")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token") from exc

    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    expected_signature = _sign(signing_input)
    if not hmac.compare_digest(expected_signature, _b64url_decode(signature_segment)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token")

    try:
        payload = json.loads(_b64url_decode(payload_segment).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token") from exc

    expires_at = int(payload.get("exp") or 0)
    if expires_at and time.time() > expires_at:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication token expired")

    return payload


def create_access_token(user: models.User) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "tenant": user.tenant_key,
        "type": "access",
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL_SECONDS,
    }
    return _create_token(payload)


def create_refresh_token(user: models.User) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "tenant": user.tenant_key,
        "type": "refresh",
        "iat": now,
        "exp": now + REFRESH_TOKEN_TTL_SECONDS,
        "jti": secrets.token_urlsafe(16),
    }
    return _create_token(payload)


def create_token_pair(user: models.User) -> dict[str, str]:
    return {
        "access_token": create_access_token(user),
        "refresh_token": create_refresh_token(user),
    }


def authenticate_user(db: Session, email: str, password: str) -> models.User | None:
    normalized_email = (email or "").strip().lower()
    if not normalized_email or not password:
        return None
    user = db.scalar(select(models.User).where(models.User.email == normalized_email))
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_or_create_default_admin(db: Session) -> models.User | None:
    bootstrap_email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    bootstrap_password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "").strip()
    if not bootstrap_email or not bootstrap_password:
        return None

    user = db.scalar(select(models.User).where(models.User.email == bootstrap_email))
    if user:
        return user

    user = models.User(
        email=bootstrap_email,
        password_hash=hash_password(bootstrap_password),
        role="admin",
        display_name="Platform Admin",
        tenant_key="bootstrap",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _extract_bearer_token(credentials: HTTPAuthorizationCredentials | None) -> str:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return credentials.credentials


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> AuthenticatedUser:
    token = _extract_bearer_token(credentials)
    payload = _decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Access token required")

    try:
        user_id = int(payload.get("sub") or 0)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token") from exc

    user = db.get(models.User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    set_current_user(user.id, user.role)
    return AuthenticatedUser(
        id=user.id,
        email=user.email,
        role=user.role,
        display_name=user.display_name or user.email,
        is_active=user.is_active,
        tenant_key=user.tenant_key,
    )


def optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> AuthenticatedUser | None:
    if not credentials:
        clear_current_user()
        return None
    try:
        return get_current_user(credentials, db)
    except HTTPException:
        clear_current_user()
        return None


def require_role(allowed: Iterable[str]):
    allowed_roles = {role.strip().lower() for role in allowed}

    def _dep(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role for this operation")
        return user

    return _dep


def optional_role() -> str | None:
    return get_current_user_role()
