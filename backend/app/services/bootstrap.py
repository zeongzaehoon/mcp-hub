import logging

from app.core.config import get_settings
from app.core.errors import Conflict
from app.domain.enums import SYSTEM_COMPANY_ID, Role
from app.domain.models import Company
from app.infra.dynamo.repository import Repository
from app.infra.redis.sync import RedisSync
from app.services.base import ServiceBase
from app.services.users import UserService

log = logging.getLogger(__name__)


async def bootstrap(repo: Repository, sync: RedisSync) -> None:
    s = get_settings()
    base = ServiceBase(repo, sync)
    sys_company = Company(company_id=SYSTEM_COMPANY_ID, name="System", created_by="bootstrap")
    try:
        await base.commit([sys_company], [repo.tx.create(sys_company)])
        log.info("created system company")
    except Conflict:
        pass
    users = UserService(repo, sync)
    if await users.get_by_email(s.master_email) is None:
        await users.create(company_id=SYSTEM_COMPANY_ID, email=s.master_email, name="Master", role=Role.MASTER, password=s.master_password)
        log.info("created master user %s", s.master_email)
