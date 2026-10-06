
import pytest

from app.core.idempotency import (
    InMemoryIdempotencyStore,
)


class TestInMemoryIdempotencyStore:
    """Tests for InMemoryIdempotencyStore."""

    @pytest.fixture
    def store(self):
        return InMemoryIdempotencyStore()

    @pytest.mark.asyncio
    async def test_first_acquire_returns_true(self, store):
        """Test that first acquire returns True."""
        result = await store.try_acquire("test-key")
        assert result is True

    @pytest.mark.asyncio
    async def test_duplicate_acquire_returns_false(self, store):
        """Test that duplicate acquire returns False."""
        await store.try_acquire("test-key")
        result = await store.try_acquire("test-key")
        assert result is False

    @pytest.mark.asyncio
    async def test_different_keys_work_independently(self, store):
        """Test that different keys work independently."""
        result1 = await store.try_acquire("key1")
        result2 = await store.try_acquire("key2")
        assert result1 is True
        assert result2 is True

    @pytest.mark.asyncio
    async def test_mark_processed(self, store):
        """Test marking key as processed."""
        await store.try_acquire("test-key")
        await store.mark_processed("test-key")

        # Should now return False for duplicate
        result = await store.try_acquire("test-key")
        assert result is False

    @pytest.mark.asyncio
    async def test_release_allows_retry(self, store):
        """Test that releasing allows retry."""
        await store.try_acquire("test-key")
        await store.release("test-key")

        result = await store.try_acquire("test-key")
        assert result is True

    @pytest.mark.asyncio
    async def test_release_only_works_on_unprocessed(self, store):
        """Test that release only works on unprocessed keys."""
        await store.try_acquire("test-key")
        await store.mark_processed("test-key")
        await store.release("test-key")

        # Should still return False (already processed)
        result = await store.try_acquire("test-key")
        assert result is False

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, store):
        """Test cleanup of expired keys."""
        import time
        # Add an old unprocessed key
        store._store["old-key"] = {"created_at": time.time() - 100000, "processed_at": None}
        # Add a recent unprocessed key
        store._store["new-key"] = {"created_at": time.time(), "processed_at": None}
        # Add a processed key (should not be cleaned up)
        store._store["processed-key"] = {"created_at": time.time() - 100000, "processed_at": time.time()}

        removed = await store.cleanup_expired(86400)  # 24 hours

        assert removed == 1
        assert "old-key" not in store._store
        assert "new-key" in store._store
        assert "processed-key" in store._store
