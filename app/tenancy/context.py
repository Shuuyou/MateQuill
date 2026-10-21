"""Authorization context is built from server-side membership, never headers."""
from contextvars import ContextVar
from dataclasses import dataclass, field
from fastapi import HTTPException

ROLES = frozenset({'admin', 'operator', 'regional_manager', 'store_manager', 'salesperson', 'reviewer'})
MANAGERS = frozenset({'admin', 'operator', 'regional_manager', 'store_manager'})
_SELECTED_SCOPE = object()


@dataclass(frozen=True)
class TenantContext:
    user_id: str
    tenant_id: str
    store_id: str | None = None
    roles: tuple[str, ...] = ()
    store_ids: tuple[str, ...] = ()
    all_stores: bool = False
    is_legacy: bool = False
    # (role, scope) from current server-side active membership rows.
    grants: tuple[tuple[str, str | None], ...] = field(default_factory=tuple)
    session_hash: str = ''

    def scoped_roles(self, store_id=_SELECTED_SCOPE):
        target = self.store_id if store_id is _SELECTED_SCOPE else store_id
        if self.is_legacy:
            return self.roles
        if self.grants:
            return tuple(sorted({role for role, scope in self.grants if scope is None or scope == target}))
        return self.roles if self.all_stores or target in self.store_ids else ()


_context: ContextVar[TenantContext | None] = ContextVar('matequill_tenant_context', default=None)


def get_context():
    return _context.get()


def require_context():
    context = get_context()
    if context is None:
        raise HTTPException(401, '请先登录工作空间')
    return context


def require_role(*roles):
    context = require_context()
    if not context.is_legacy and not set(context.scoped_roles()).intersection(roles):
        raise HTTPException(403, '当前账号没有此操作权限')
    return context


def authorize_scope(store_id, action='read'):
    context = require_context()
    if context.is_legacy:
        return context
    if store_id is None:
        if not context.all_stores:
            raise HTTPException(403, '请先选择有权限的门店')
    elif not context.all_stores and store_id not in context.store_ids:
        raise HTTPException(404, '没有找到这家门店')
    if action in {'manage', 'review', 'approve'}:
        allowed = MANAGERS | ({'reviewer'} if action in {'review', 'approve'} else set())
        if not set(context.scoped_roles(store_id)).intersection(allowed):
            raise HTTPException(403, '当前账号没有此操作权限')
    return context


def set_context(context):
    return _context.set(context)


def reset_context(token):
    _context.reset(token)
