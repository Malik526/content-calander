"""dependencies/storage.py — get_storage(): the API layer's one entry
point for "the configured object-storage backend" (Milestone 3.7).

Mirrors get_store() in dependencies/auth.py exactly — never a hardcoded
LocalStorage/SupabaseStorage, always storage.factory.build_storage(), which
already fails loudly on a misconfigured hosted backend rather than
silently falling back to local (see that factory's own docstring). No
context manager needed: StorageProtocol has no close()/__enter__ — both
implementations are stateless wrappers (a filesystem root, or plain
`requests` calls), unlike ContentStoreProtocol's real connection handle."""

from content_automation.storage.factory import build_storage
from content_automation.storage.protocol import StorageProtocol


def get_storage() -> StorageProtocol:
    return build_storage()
