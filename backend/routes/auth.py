from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import (
    AuthenticatedUser,
    authenticate_user,
    create_token_pair,
    get_current_user,
    hash_password,
)
from ..database import get_db


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=schemas.AuthResponse)
def signup(payload: schemas.SignUpRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    if db.scalar(select(models.User).where(models.User.email == email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered")

    role = (payload.role or "teacher").strip().lower()
    if role not in {"teacher", "student", "parent", "admin"}:
        role = "teacher"

    user = models.User(
        email=email,
        password_hash=hash_password(payload.password),
        role=role,
        display_name=payload.display_name or payload.email.split("@")[0],
        tenant_key=payload.tenant_key or (email.split("@")[1] if "@" in email else email),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    tokens = create_token_pair(user)
    return {
        **tokens,
        "user": {
            "id": user.id,
            "email": user.email,
            "role": user.role,
            "display_name": user.display_name,
            "tenant_key": user.tenant_key,
        },
    }


@router.post("/login", response_model=schemas.AuthResponse)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    tokens = create_token_pair(user)
    return {
        **tokens,
        "user": {
            "id": user.id,
            "email": user.email,
            "role": user.role,
            "display_name": user.display_name,
            "tenant_key": user.tenant_key,
        },
    }


@router.post("/refresh", response_model=schemas.TokenPair)
def refresh(payload: schemas.RefreshTokenRequest, db: Session = Depends(get_db)):
    from ..auth import _decode_token

    token_data = _decode_token(payload.refresh_token)
    if token_data.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token required")

    user_id = int(token_data.get("sub") or 0)
    user = db.get(models.User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    return create_token_pair(user)


@router.get("/me", response_model=schemas.UserProfile)
def me(user: AuthenticatedUser = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "display_name": user.display_name,
        "tenant_key": user.tenant_key,
    }


@router.post("/logout")
def logout(_user: AuthenticatedUser = Depends(get_current_user)):
    return {"status": "ok"}