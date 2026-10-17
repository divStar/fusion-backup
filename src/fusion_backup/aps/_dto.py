"""Pydantic models of the APS responses this package reads (private: never returned to callers).

Only the fields actually used are declared; everything else in Autodesk's answers is ignored. Field
names are snake_case and map to Autodesk's camelCase automatically. The answers that differ between the
Manufacturing Data Model API versions are modelled next to their queries, in `mfg_data_model.v2` and `.v3`.
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Dto(BaseModel):
    """Base of every response model."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore", frozen=True)


# --- Data Management API (JSON:API documents) ---------------------------------------------------


class Reference(Dto):
    id: str


class Relationship(Dto):
    data: Reference


class NamedAttributes(Dto):
    name: str


class NamedResource(Dto):
    """A hub or a project."""

    id: str
    attributes: NamedAttributes


class NamedResourceList(Dto):
    data: list[NamedResource]


class ProjectRelationships(Dto):
    root_folder: Relationship


class ProjectResource(Dto):
    relationships: ProjectRelationships


class ProjectDocument(Dto):
    data: ProjectResource


class Extension(Dto):
    type: str


class EntryAttributes(Dto):
    display_name: str
    extension: Extension | None = None


class EntryRelationships(Dto):
    tip: Relationship | None = None


class FolderEntry(Dto):
    """An item (file) or a subfolder inside a folder."""

    type: str
    id: str
    attributes: EntryAttributes
    relationships: EntryRelationships = EntryRelationships()


class VersionAttributes(Dto):
    version_number: int


class VersionRelationships(Dto):
    storage: Relationship | None = None


class Version(Dto):
    type: str
    id: str
    attributes: VersionAttributes
    relationships: VersionRelationships = VersionRelationships()


class VersionDocument(Dto):
    data: Version


class Link(Dto):
    href: str


class PageLinks(Dto):
    next: Link | None = None


class FolderContents(Dto):
    """One page of a folder listing; `included` carries the tip version of every item on the page."""

    data: list[FolderEntry]
    included: list[Version] = []
    links: PageLinks = PageLinks()


class SignedDownload(Dto):
    url: str


# --- Manufacturing Data Model API (GraphQL) -----------------------------------------------------


class GraphQLError(Dto):
    message: str


class MfgHub(Dto):
    id: str
    name: str


class MfgHubResults(Dto):
    results: list[MfgHub]


class MfgHubsData(Dto):
    hubs: MfgHubResults


class Derivative(Dto):
    output_format: str
    status: str | None = None
    signed_url: str | None = None
