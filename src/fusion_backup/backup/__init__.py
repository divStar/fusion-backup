"""The backup itself: target configuration and state, the run, and the status report.

Works against `fusion_backup.cloud.CloudSource` only; which cloud implementation is used is decided by
the caller.
"""

from fusion_backup.backup.report import Summary
from fusion_backup.backup.run import BackupRun, RunOptions
from fusion_backup.backup.selection import ItemSelection
from fusion_backup.backup.status import status_lines
from fusion_backup.backup.target import BackupError, BackupTarget

__all__ = ["BackupError", "BackupRun", "BackupTarget", "ItemSelection", "RunOptions", "Summary", "status_lines"]
