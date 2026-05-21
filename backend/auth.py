from fastapi import Header, HTTPException


def require_role(allowed: list[str]):
    def _dep(x_user_role: str | None = Header(None, alias="X-User-Role")):
        role = (x_user_role or "").strip().lower()
        if role not in [r.lower() for r in allowed]:
            raise HTTPException(status_code=403, detail="Insufficient role for this operation")
        return role
    return _dep


def optional_role(x_user_role: str | None = None):
    return (x_user_role or "").strip().lower()
