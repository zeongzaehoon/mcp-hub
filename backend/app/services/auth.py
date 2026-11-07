from dataclasses import dataclass

from app.core.errors import Unauthorized
from app.core.security import issue_jwt, verify_password
from app.domain.enums import Role, Status
from app.domain.models import User
from app.services.users import UserService


@dataclass(frozen=True)
class Principal:
    user_id: str
    company_id: str
    role: Role
    email: str

    @property
    def is_master(self) -> bool:
        return self.role == Role.MASTER


class AuthService:
    def __init__(self, users: UserService):
        self.users = users

    async def login(self, email: str, password: str) -> tuple[str, User]:
        u = await self.users.get_by_email(email)
        if not u or not u.password_hash or u.status != Status.ACTIVE or not verify_password(password, u.password_hash):
            raise Unauthorized("invalid credentials")
        token = issue_jwt({"sub": u.user_id, "cid": u.company_id, "role": u.role, "email": u.email})
        return token, u
