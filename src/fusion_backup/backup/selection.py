"""Choosing which cloud items a run or status report handles."""

from dataclasses import dataclass
from fnmatch import fnmatchcase

from fusion_backup.cloud import CloudItem


@dataclass(frozen=True)
class ItemSelection:
    """All items, or those whose path (as shown by `list`) matches one of the patterns.

    Patterns are case-sensitive; `*` and `?` work as in the shell, `*` also across folders.
    """

    patterns: tuple[str, ...] = ()

    def includes(self, item: CloudItem) -> bool:
        return not self.patterns or any(fnmatchcase(item.path, pattern) for pattern in self.patterns)

    def unmatched(self, items: list[CloudItem]) -> list[str]:
        """Patterns that match none of the given items."""
        return [pattern for pattern in self.patterns if not any(fnmatchcase(item.path, pattern) for item in items)]


ALL_ITEMS = ItemSelection()
