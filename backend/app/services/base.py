from app.infra.dynamo.keys import Entity
from app.infra.dynamo.repository import Repository
from app.infra.redis.sync import RedisSync
from app.services.outbox import Outbox


class ServiceBase:
    def __init__(self, repo: Repository, sync: RedisSync):
        self.repo = repo
        self.sync = sync
        self.outbox = Outbox(repo, sync)

    async def commit(self, entities: list[Entity], extra_ops: list[dict] | None = None) -> None:
        """Write entities (already prepared as create/replace ops in extra_ops) + their outbox rows
        in one transaction, then sync inline. Callers pass the entity ops; this adds outbox rows."""
        ops = list(extra_ops or [])
        items = []
        for e in entities:
            op, item = self.outbox.op_for(e)
            ops.append(op)
            items.append(item)
        await self.repo.transact(ops)
        await self.outbox.flush_inline(entities, items)

    @staticmethod
    def bump(e: Entity) -> int:
        """Increments version in place; returns the version to expect in the condition."""
        from app.domain.models import now

        expected = e.version
        e.version += 1
        e.updated_at = now()
        return expected
