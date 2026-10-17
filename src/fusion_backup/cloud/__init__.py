"""The cloud API as the backup sees it: plain domain objects and the `CloudSource` interface.

Nothing in here knows about Autodesk's endpoints, JSON formats or authentication; that is the job of
an implementation such as `fusion_backup.aps.ApsCloudSource`.
"""

from fusion_backup.cloud.model import CloudError, CloudItem, CloudProject, CloudSource, StepUnavailableError

__all__ = ["CloudError", "CloudItem", "CloudProject", "CloudSource", "StepUnavailableError"]
