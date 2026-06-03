"""
Async SQLAlchemy engine + session factory for Hermes.

Hermes connects to the SAME Postgres instance as the Backend API — they
share tables (tenant_memory, agent_states, agent_runs, marketplace_cabinets).
Migrations are owned by Backend (alembic/); Hermes is a read/write client only.

RLS context (migration 0006):
  Multi-tenant tables have a `tenant_isolation` policy that filters rows
  by `current_setting('app.current_tenant')`. Hermes is server-side, so
  every query must SET LOCAL the var before reading/writing. Use the
  `tenant_session(tenant_id)` async context manager below — it opens a
  session, sets the var, and tears down cleanly.
"""
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings

# Supabase Session Pooler routes through pgBouncer, which doesn't support
# server-side prepared statements. Disable asyncpg's statement cache for
# pgBouncer hosts to avoid stale prepared-statement errors.
_IS_PGBOUNCER = "pooler.supabase.com" in settings.DATABASE_URL
_CONNECT_ARGS = {"statement_cache_size": 0} if _IS_PGBOUNCER else {}

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    connect_args=_CONNECT_ARGS,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncSession:
    """Yields an AsyncSession. Use with `async with` in skills."""
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def tenant_session(
    tenant_id: Optional[str], *, admin: bool = False
) -> AsyncIterator[AsyncSession]:
    """Open a session with RLS context set.

    Pass `tenant_id` (UUID string) so the session can only see that
    tenant's rows. Pass `admin=True` for cross-tenant operations like
    cron polling — sets `app.current_tenant='admin'` which is the only
    literal the tenant_isolation policy bypasses.

    Usage:
        async with tenant_session(self.tenant_id) as s:
            result = await s.execute(text("SELECT ..."))
            await s.commit()  # callers handle commit for writes

    SQLAlchemy auto-begins a transaction on the first execute(), and
    SET LOCAL is transaction-scoped, so subsequent statements in the
    same session inherit the binding. Callers must call s.commit()
    explicitly for writes (same contract as the previous raw
    AsyncSessionLocal usage).
    """
    async with AsyncSessionLocal() as session:
        ctx = "admin" if admin else (tenant_id or "")
        # 1) SET LOCAL ROLE → mao_app (NOBYPASSRLS). The default Supabase
        #    `postgres` role has rolbypassrls=true, which would render
        #    every policy a no-op. Migration 0007 grants `mao_app` to
        #    `postgres` so SET ROLE works without superuser.
        # 2) SET LOCAL app.current_tenant → ctx. The `tenant_isolation`
        #    policy filters every row by it; literal 'admin' bypasses.
        # SET LOCAL is tx-scoped — both auto-revert on session close.
        # tenant_id comes from a verified JWT (passed by Backend) or is
        # a literal we control — no injection surface.
        await session.execute(text("SET LOCAL ROLE mao_app"))
        await session.execute(
            text(f"SET LOCAL app.current_tenant = '{ctx}'")
        )
        yield session
