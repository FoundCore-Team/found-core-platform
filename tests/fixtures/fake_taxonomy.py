from app.domain.entities import (
    CategoryEntity,
    NewTagEntity,
    NewTagScopeEntity,
    RoleEntity,
    RoleFieldEntity,
    TagEntity,
)
from app.domain.enums import TagStatus
from app.domain.interfaces import ITaxonomyCacheRepository, ITaxonomyRepository


class FakeTaxonomyRepository(ITaxonomyRepository):
    """In-memory stand-in for SqlAlchemyTaxonomyRepository. Implements the real
    ABC so a signature drift there fails the unit tests too.

    `lose_race_once` simulates a concurrent request winning the INSERT: the next
    `create_tag` call stores the tag as if another request had inserted it first
    and returns None, exactly like `ON CONFLICT DO NOTHING RETURNING` does.
    """

    def __init__(
        self,
        categories: list[CategoryEntity] | None = None,
        roles: list[RoleEntity] | None = None,
        role_fields: list[RoleFieldEntity] | None = None,
        tags: list[TagEntity] | None = None,
    ) -> None:
        self.categories = list(categories or [])
        self.roles = list(roles or [])
        self.role_fields = list(role_fields or [])
        self.tags: dict[int, TagEntity] = {t.id: t for t in (tags or [])}
        self.scopes: list[NewTagScopeEntity] = []
        self.lose_race_once = False
        self.calls: list[str] = []
        self.suggest_calls: list[dict] = []
        self._next_tag_id = max(self.tags, default=0) + 1

    def _record(self, name: str) -> None:
        self.calls.append(name)

    async def get_categories(self) -> list[CategoryEntity]:
        self._record("get_categories")
        return list(self.categories)

    async def get_category_by_id(self, category_id: int) -> CategoryEntity | None:
        self._record("get_category_by_id")
        return next((c for c in self.categories if c.id == category_id), None)

    async def get_roles_by_category(self, category_id: int) -> list[RoleEntity]:
        self._record("get_roles_by_category")
        return [r for r in self.roles if r.category_id == category_id]

    async def get_role_by_id(self, role_id: int) -> RoleEntity | None:
        self._record("get_role_by_id")
        return next((r for r in self.roles if r.id == role_id), None)

    async def get_role_fields_by_role(self, role_id: int) -> list[RoleFieldEntity]:
        self._record("get_role_fields_by_role")
        return [f for f in self.role_fields if f.role_id == role_id]

    async def suggest_tags(
        self, query: str, category_id: int, role_id: int | None, user_id: int
    ) -> list[TagEntity]:
        self._record("suggest_tags")
        self.suggest_calls.append({"query": query, "category_id": category_id, "role_id": role_id, "user_id": user_id})
        return [t for t in self.tags.values() if query.lower() in t.title.lower()]

    async def get_tag_by_normalized_title(self, normalized_title: str) -> TagEntity | None:
        self._record("get_tag_by_normalized_title")
        return next((t for t in self.tags.values() if t.normalized_title == normalized_title), None)

    async def get_tags_in_scope_by_ids(
        self, tag_ids: list[int], category_id: int, role_id: int
    ) -> list[TagEntity]:
        self._record("get_tags_in_scope_by_ids")
        in_scope = {
            s.tag_id for s in self.scopes if s.category_id == category_id and s.role_id in (None, role_id)
        }
        return [t for t in sorted(self.tags.values(), key=lambda t: t.id) if t.id in tag_ids and t.id in in_scope]

    async def get_tags_by_ids(self, tag_ids: list[int]) -> list[TagEntity]:
        self._record("get_tags_by_ids")
        return [t for t in sorted(self.tags.values(), key=lambda t: t.id) if t.id in tag_ids]

    async def create_tag(self, tag: NewTagEntity) -> TagEntity | None:
        self._record("create_tag")
        exists = any(t.normalized_title == tag.normalized_title for t in self.tags.values())
        if self.lose_race_once and not exists:
            self.lose_race_once = False
            # Another request inserts the same title first (by some other user).
            self._store(tag.title, tag.normalized_title, TagStatus.PENDING, created_by_user_id=None)
            return None
        if exists:
            return None
        return self._store(tag.title, tag.normalized_title, tag.status, tag.created_by_user_id)

    async def increment_tags_usage(self, tag_ids: list[int]) -> None:
        self._record("increment_tags_usage")
        for tag_id in tag_ids:
            self.tags[tag_id].usage_count += 1

    async def create_tag_scope(self, scope: NewTagScopeEntity) -> None:
        self._record("create_tag_scope")
        if scope not in self.scopes:
            self.scopes.append(scope)

    def _store(
        self, title: str, normalized_title: str, status: TagStatus, created_by_user_id: int | None
    ) -> TagEntity:
        entity = TagEntity(
            id=self._next_tag_id,
            title=title,
            normalized_title=normalized_title,
            status=status,
            created_by_user_id=created_by_user_id,
        )
        self.tags[entity.id] = entity
        self._next_tag_id += 1
        return entity


class FakeTaxonomyCacheRepository(ITaxonomyCacheRepository):
    """Dict-backed cache. With `outage=True` it behaves like the Redis
    implementation during a Redis outage: reads return None, writes are no-ops."""

    def __init__(self, outage: bool = False) -> None:
        self.outage = outage
        self._store: dict[str, object] = {}
        self.calls: list[str] = []

    async def _get(self, name: str, key: str):
        self.calls.append(name)
        return None if self.outage else self._store.get(key)

    async def _set(self, name: str, key: str, value: object) -> None:
        self.calls.append(name)
        if not self.outage:
            self._store[key] = value

    async def get_categories(self) -> list[CategoryEntity] | None:
        return await self._get("get_categories", "categories")

    async def set_categories(self, categories: list[CategoryEntity]) -> None:
        await self._set("set_categories", "categories", categories)

    async def get_roles_by_category(self, category_id: int) -> list[RoleEntity] | None:
        return await self._get("get_roles_by_category", f"roles:{category_id}")

    async def set_roles_by_category(self, category_id: int, roles: list[RoleEntity]) -> None:
        await self._set("set_roles_by_category", f"roles:{category_id}", roles)

    async def get_role_fields_by_role(self, role_id: int) -> list[RoleFieldEntity] | None:
        return await self._get("get_role_fields_by_role", f"fields:{role_id}")

    async def set_role_fields_by_role(self, role_id: int, fields: list[RoleFieldEntity]) -> None:
        await self._set("set_role_fields_by_role", f"fields:{role_id}", fields)
