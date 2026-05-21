from contextvars import ContextVar


current_user_id: ContextVar[int | None] = ContextVar("current_user_id", default=None)
current_user_role: ContextVar[str | None] = ContextVar("current_user_role", default=None)


def set_current_user(user_id: int | None, role: str | None = None) -> None:
    current_user_id.set(user_id)
    current_user_role.set((role or "").strip().lower() or None)


def clear_current_user() -> None:
    current_user_id.set(None)
    current_user_role.set(None)


def get_current_user_id() -> int | None:
    return current_user_id.get()


def get_current_user_role() -> str | None:
    return current_user_role.get()