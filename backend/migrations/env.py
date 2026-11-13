import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.database.base import Base
import app.models  # noqa: F401  (register models)

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    # Read DATABASE_URL directly so migrations don't require provider API keys.
    return os.environ.get("DATABASE_URL", "postgresql+psycopg://scout:scout@localhost:5433/scout")


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
