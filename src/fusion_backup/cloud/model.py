"""Domain objects and the interface every cloud implementation provides."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class CloudError(Exception):
    """The cloud could not answer a request; the message is meant for the user."""


class StepUnavailableError(CloudError):
    """STEP generation was refused before it started, e.g. because the monthly free limit is used up."""


@dataclass(frozen=True)
class CloudProject:
    """A project the login can see, named by its hub and its own name."""

    hub: str
    name: str


@dataclass(frozen=True)
class CloudItem:
    """The newest version of one file in a project."""

    hub: str
    project: str
    path: str  # inside the project, e.g. "Chassis bracket" or "Parts/drawing.pdf"
    item_id: str  # stable across versions; opaque to everything but the implementation
    is_design: bool  # a Fusion design (downloads as .f3d, can be exported to STEP) or any other file
    version: int


class CloudSource(Protocol):
    """What the backup needs from the cloud."""

    def list_projects(self) -> list[CloudProject]:
        """All projects the login can see."""
        ...

    def list_items(self, hub: str, project: str) -> list[CloudItem]:
        """Every file of a project, including subfolders, at its newest version, sorted by path."""
        ...

    def download(self, item: CloudItem) -> bytes:
        """The stored file of the item's version; the native .f3d for designs."""
        ...

    def generate_step(self, item: CloudItem, on_submitted: Callable[[], None]) -> bytes:
        """A STEP export of the design's version.

        `on_submitted` is called as soon as the cloud has accepted the export request, i.e. when it counts
        as a metered job, even if the export fails later. Raises StepUnavailableError if the request is
        refused, CloudError for any other failure.
        """
        ...
