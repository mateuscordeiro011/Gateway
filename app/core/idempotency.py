import time
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(primary_key=True)
    created_at: Mapped[float] = mapped_column(default=time.time)
    processed_at: Mapped[float | None] = mapped_column(nullable=True)


class IdempotencyStore(ABC):
    """Abstract base class for idempotency stores."""

    @abstractmethod
    async def try_acquire(self, key: str) -> bool:
        """
        Try to acquire the lock for a key.

        Returns:
            True if this is the first time seeing this key (should process)
            False if key already exists (duplicate, should not process)
        """
        pass

    @abstractmethod
    async def mark_processed(self, key: str) -> None:
        """Mark a key as successfully processed."""
        pass

    @abstractmethod
    async def release(self, key: str) -> None:
        """Release a key (allow retry) - used when processing fails."""
        pass

    @abstractmethod
    async def cleanup_expired(self, ttl_seconds: int) -> int:
        """Remove expired keys. Returns count of removed keys."""
        pass


class DatabaseIdempotencyStore(IdempotencyStore):
    """Database-backed idempotency store using unique constraint."""

    def __init__(self, database_url: str | None = None):
        settings = get_settings()
        self.database_url = database_url or settings.DATABASE_URL
        self.engine = None
        self.session_factory = None

    async def initialize(self) -> None:
        """Initialize the database connection and create tables."""
        self.engine = create_async_engine(
            self.database_url,
            echo=False,
            pool_pre_ping=True,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # Create tables
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """Close the database connection."""
        if self.engine:
            await self.engine.dispose()

    @asynccontextmanager
    async def _session(self) -> AsyncSession:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def try_acquire(self, key: str) -> bool:
        """Try to insert the key. Returns True if inserted (first time), False if exists."""
        async with self._session() as session:
            # Try to insert - will fail on duplicate key due to unique constraint
            new_key = IdempotencyKey(key=key, created_at=time.time())
            session.add(new_key)
            try:
                await session.commit()
                return True
            except Exception:
                # Duplicate key - already exists
                await session.rollback()
                # Check if already processed
                existing = await session.get(IdempotencyKey, key)
                if existing and existing.processed_at is not None:
                    return False  # Already processed
                # Exists but not processed - another request is processing it
                return False

    async def mark_processed(self, key: str) -> None:
        """Mark a key as successfully processed."""
        async with self._session() as session:
            existing = await session.get(IdempotencyKey, key)
            if existing:
                existing.processed_at = time.time()
                await session.commit()

    async def release(self, key: str) -> None:
        """Release a key (delete it) to allow retry."""
        async with self._session() as session:
            existing = await session.get(IdempotencyKey, key)
            if existing and existing.processed_at is None:
                await session.delete(existing)
                await session.commit()

    async def cleanup_expired(self, ttl_seconds: int) -> int:
        """Remove expired unprocessed keys."""
        cutoff = time.time() - ttl_seconds
        async with self._session() as session:
            result = await session.execute(
                select(IdempotencyKey).where(
                    IdempotencyKey.created_at < cutoff,
                    IdempotencyKey.processed_at.is_(None)
                )
            )
            keys = result.scalars().all()
            count = len(keys)
            for key in keys:
                await session.delete(key)
            await session.commit()
            return count


class InMemoryIdempotencyStore(IdempotencyStore):
    """In-memory idempotency store for testing/development without database."""

    def __init__(self):
        self._store: dict[str, dict] = {}

    async def try_acquire(self, key: str) -> bool:
        if key in self._store:
            entry = self._store[key]
            if entry.get("processed_at") is not None:
                return False  # Already processed
            return False  # Being processed

        self._store[key] = {"created_at": time.time(), "processed_at": None}
        return True

    async def mark_processed(self, key: str) -> None:
        if key in self._store:
            self._store[key]["processed_at"] = time.time()

    async def release(self, key: str) -> None:
        if key in self._store and self._store[key].get("processed_at") is None:
            del self._store[key]

    async def cleanup_expired(self, ttl_seconds: int) -> int:
        cutoff = time.time() - ttl_seconds
        to_delete = [
            k for k, v in self._store.items()
            if v["created_at"] < cutoff and v.get("processed_at") is None
        ]
        for k in to_delete:
            del self._store[k]
        return len(to_delete)


async def get_idempotency_store() -> IdempotencyStore:
    """Factory function to get the appropriate idempotency store."""
    settings = get_settings()

    if settings.IDEMPOTENCY_BACKEND == "database":
        store = DatabaseIdempotencyStore()
        await store.initialize()
        return store
    else:
        return InMemoryIdempotencyStore()
