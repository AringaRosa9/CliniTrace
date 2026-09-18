from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.db import s1, s2, s3  # noqa: F401
from app.db.models import metadata

if context.is_offline_mode():
    context.configure(url=get_settings().database_url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
