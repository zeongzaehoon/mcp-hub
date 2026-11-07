"""Thin async repository over the low-level DynamoDB client.

Design rules:
  * every multi-item mutation goes through `transact()` — never two separate writes;
  * creates use attribute_not_exists(PK), updates use `version = :expected` (optimistic lock);
  * point reads are strongly consistent; GSI reads are eventually consistent and used only for listing.
"""

import base64
from decimal import Decimal
from typing import Any

import orjson
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.core.errors import Conflict
from app.infra.dynamo.client import DynamoClientFactory
from app.infra.dynamo.keys import Entity, entity_keys, from_item, to_item

_ser = TypeSerializer()
_de = TypeDeserializer()


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v % 1 == 0 else float(v)
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def serialize(d: dict[str, Any]) -> dict[str, Any]:
    return {k: _ser.serialize(v) for k, v in d.items() if v is not None}


def deserialize(d: dict[str, Any]) -> dict[str, Any]:
    return _plain({k: _de.deserialize(v) for k, v in d.items()})


def encode_cursor(key: dict[str, Any] | None) -> str | None:
    return base64.urlsafe_b64encode(orjson.dumps(key)).decode() if key else None


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    return serialize(orjson.loads(base64.urlsafe_b64decode(cursor)))


class TxOp:
    """Builder for TransactWriteItems entries."""

    def __init__(self, table: str):
        self.table = table

    def create(self, e: Entity) -> dict:
        return {
            "Put": {
                "TableName": self.table,
                "Item": serialize(to_item(e)),
                "ConditionExpression": "attribute_not_exists(PK)",
            }
        }

    def replace(self, e: Entity, expected_version: int) -> dict:
        """Full-item replace guarded by optimistic version. Caller must have bumped e.version."""
        return {
            "Put": {
                "TableName": self.table,
                "Item": serialize(to_item(e)),
                "ConditionExpression": "attribute_exists(PK) AND version = :v",
                "ExpressionAttributeValues": serialize({":v": expected_version}),
            }
        }

    def put_raw(self, item: dict[str, Any], *, must_not_exist: bool = False) -> dict:
        op: dict[str, Any] = {"TableName": self.table, "Item": serialize(item)}
        if must_not_exist:
            op["ConditionExpression"] = "attribute_not_exists(PK)"
        return {"Put": op}

    def delete_raw(self, pk: str, sk: str) -> dict:
        return {"Delete": {"TableName": self.table, "Key": serialize({"PK": pk, "SK": sk})}}

    def check_exists(self, pk: str, sk: str, *, status: str | None = None, extra: dict[str, Any] | None = None) -> dict:
        cond = ["attribute_exists(PK)"]
        vals: dict[str, Any] = {}
        if status:
            cond.append("#st = :st")
            vals[":st"] = status
        for i, (k, v) in enumerate((extra or {}).items()):
            cond.append(f"{k} = :x{i}")
            vals[f":x{i}"] = v
        op: dict[str, Any] = {
            "TableName": self.table,
            "Key": serialize({"PK": pk, "SK": sk}),
            "ConditionExpression": " AND ".join(cond),
        }
        if status:
            op["ExpressionAttributeNames"] = {"#st": "status"}
        if vals:
            op["ExpressionAttributeValues"] = serialize(vals)
        return {"ConditionCheck": op}


class Repository:
    def __init__(self, factory: DynamoClientFactory):
        self.factory = factory
        self.table = get_settings().table_name
        self.log_table = get_settings().log_table_name
        self.tx = TxOp(self.table)

    async def get(self, pk: str, sk: str) -> Entity | None:
        async with self.factory.client() as c:
            r = await c.get_item(TableName=self.table, Key=serialize({"PK": pk, "SK": sk}), ConsistentRead=True)
        item = r.get("Item")
        return from_item(deserialize(item)) if item else None

    async def get_raw(self, pk: str, sk: str) -> dict[str, Any] | None:
        async with self.factory.client() as c:
            r = await c.get_item(TableName=self.table, Key=serialize({"PK": pk, "SK": sk}), ConsistentRead=True)
        item = r.get("Item")
        return deserialize(item) if item else None

    async def query(
        self,
        pk: str,
        *,
        sk_prefix: str | None = None,
        index: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        filters: dict[str, Any] | None = None,
        contains: dict[str, str] | None = None,
        consistent: bool = False,
    ) -> tuple[list[Entity], str | None]:
        pk_attr, sk_attr = ("PK", "SK") if not index else (f"{index}pk", f"{index}sk")
        kce = "#pk = :pk" + (" AND begins_with(#sk, :skp)" if sk_prefix else "")
        names = {"#pk": pk_attr}
        values: dict[str, Any] = {":pk": pk}
        if sk_prefix:
            names["#sk"] = sk_attr
            values[":skp"] = sk_prefix
        fexpr: list[str] = []
        for i, (k, v) in enumerate((filters or {}).items()):
            names[f"#f{i}"] = k
            values[f":f{i}"] = v
            fexpr.append(f"#f{i} = :f{i}")
        for i, (k, v) in enumerate((contains or {}).items()):
            names[f"#c{i}"] = k
            values[f":c{i}"] = v
            fexpr.append(f"contains(#c{i}, :c{i})")
        params: dict[str, Any] = {
            "TableName": self.table,
            "KeyConditionExpression": kce,
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": serialize(values),
            "Limit": limit,
            "ConsistentRead": consistent and not index,
        }
        if index:
            params["IndexName"] = index
        if fexpr:
            params["FilterExpression"] = " AND ".join(fexpr)
        start = decode_cursor(cursor)
        if start:
            params["ExclusiveStartKey"] = start
        items: list[Entity] = []
        next_cursor: str | None = None
        async with self.factory.client() as c:
            # filtered pages may come back short/empty; keep paging until we have `limit` or run out
            while True:
                r = await c.query(**params)
                items.extend(from_item(deserialize(i)) for i in r.get("Items", []) if "entity_type" in i)
                lek = r.get("LastEvaluatedKey")
                if not lek:
                    next_cursor = None
                    break
                if len(items) >= limit:
                    next_cursor = encode_cursor(deserialize(lek))
                    break
                params["ExclusiveStartKey"] = lek
        return items, next_cursor

    async def scan_entities(self, *, entity_type: str | None = None):
        """Async generator over all entities (reconciler only)."""
        params: dict[str, Any] = {"TableName": self.table}
        if entity_type:
            params["FilterExpression"] = "entity_type = :t"
            params["ExpressionAttributeValues"] = serialize({":t": entity_type})
        async with self.factory.client() as c:
            while True:
                r = await c.scan(**params)
                for i in r.get("Items", []):
                    d = deserialize(i)
                    if "entity_type" in d:
                        yield from_item(d)
                lek = r.get("LastEvaluatedKey")
                if not lek:
                    return
                params["ExclusiveStartKey"] = lek

    async def scan_raw(self, pk: str):
        """All raw items in a partition (outbox)."""
        params: dict[str, Any] = {
            "TableName": self.table,
            "KeyConditionExpression": "PK = :pk",
            "ExpressionAttributeValues": serialize({":pk": pk}),
            "ConsistentRead": True,
        }
        async with self.factory.client() as c:
            while True:
                r = await c.query(**params)
                for i in r.get("Items", []):
                    yield deserialize(i)
                lek = r.get("LastEvaluatedKey")
                if not lek:
                    return
                params["ExclusiveStartKey"] = lek

    async def transact(self, ops: list[dict]) -> None:
        if not ops:
            return
        if len(ops) > 100:
            raise ValueError("transaction exceeds 100 items; chunk it")
        async with self.factory.client() as c:
            try:
                await c.transact_write_items(TransactItems=ops)
            except ClientError as e:
                code = e.response["Error"]["Code"]
                if code == "TransactionCanceledException":
                    reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
                    raise Conflict(
                        "transaction cancelled: a precondition failed (stale version, missing parent, "
                        f"inactive parent or duplicate). reasons={reasons}",
                        code="tx_cancelled",
                    ) from e
                raise

    async def delete_raw(self, pk: str, sk: str) -> None:
        async with self.factory.client() as c:
            await c.delete_item(TableName=self.table, Key=serialize({"PK": pk, "SK": sk}))

    async def batch_put_logs(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        async with self.factory.client() as c:
            for i in range(0, len(items), 25):
                chunk = items[i : i + 25]
                req = {self.log_table: [{"PutRequest": {"Item": serialize(x)}} for x in chunk]}
                while req:
                    r = await c.batch_write_item(RequestItems=req)
                    req = r.get("UnprocessedItems") or {}

    async def query_logs(self, pk: str, *, index: str | None, start: str | None, end: str | None, limit: int, cursor: str | None):
        pk_attr, sk_attr = ("PK", "SK") if not index else (f"{index}pk", f"{index}sk")
        kce = "#pk = :pk"
        values: dict[str, Any] = {":pk": pk}
        if start and end:
            kce += " AND #sk BETWEEN :s AND :e"
            values.update({":s": start, ":e": end})
        elif start:
            kce += " AND #sk >= :s"
            values[":s"] = start
        elif end:
            kce += " AND #sk <= :e"
            values[":e"] = end
        params: dict[str, Any] = {
            "TableName": self.log_table,
            "KeyConditionExpression": kce,
            "ExpressionAttributeNames": {"#pk": pk_attr, **({"#sk": sk_attr} if (start or end) else {})},
            "ExpressionAttributeValues": serialize(values),
            "Limit": limit,
            "ScanIndexForward": False,
        }
        if index:
            params["IndexName"] = index
        s = decode_cursor(cursor)
        if s:
            params["ExclusiveStartKey"] = s
        async with self.factory.client() as c:
            r = await c.query(**params)
        return [deserialize(i) for i in r.get("Items", [])], encode_cursor(deserialize(r["LastEvaluatedKey"]) if r.get("LastEvaluatedKey") else None)

    @staticmethod
    def keys_of(e: Entity) -> tuple[str, str]:
        k = entity_keys(e)
        return k["PK"], k["SK"]
