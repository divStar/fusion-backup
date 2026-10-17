"""APS Data Management API: hubs, projects, folders, item versions and downloads. Free of charge."""

import urllib.parse
from collections.abc import Iterator

from fusion_backup.aps._dto import FolderContents, NamedResource, NamedResourceList, ProjectDocument, SignedDownload, VersionDocument
from fusion_backup.aps.client import ApsClient


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class DataManagementApi:
    """The Data Management endpoints used for listing and downloading."""

    def __init__(self, client: ApsClient):
        self._client = client

    def hubs(self) -> list[NamedResource]:
        return self._client.get("/project/v1/hubs", NamedResourceList).data

    def projects(self, hub_id: str) -> list[NamedResource]:
        return self._client.get(f"/project/v1/hubs/{_quote(hub_id)}/projects", NamedResourceList).data

    def root_folder_id(self, hub_id: str, project_id: str) -> str:
        project = self._client.get(f"/project/v1/hubs/{_quote(hub_id)}/projects/{_quote(project_id)}", ProjectDocument)
        return project.data.relationships.root_folder.data.id

    def folder_contents(self, project_id: str, folder_id: str) -> Iterator[FolderContents]:
        """All pages of a folder listing, following the `next` links."""
        url: str | None = f"/data/v1/projects/{_quote(project_id)}/folders/{_quote(folder_id)}/contents"
        while url:
            page = self._client.get(url, FolderContents)
            yield page
            url = page.links.next.href if page.links.next else None

    def tip_version(self, project_id: str, item_id: str) -> int:
        """The version number of the item's current (tip) version."""
        tip = self._client.get(f"/data/v1/projects/{_quote(project_id)}/items/{_quote(item_id)}/tip", VersionDocument)
        return tip.data.attributes.version_number

    def download(self, storage_urn: str) -> bytes:
        """Download a stored file via a pre-signed S3 URL."""
        # storage URN format: urn:adsk.objects:os.object:<bucket>/<object>
        bucket, object_name = storage_urn.split("os.object:", 1)[1].split("/", 1)
        signed = self._client.get(f"/oss/v2/buckets/{bucket}/objects/{_quote(object_name)}/signeds3download", SignedDownload)
        return self._client.download(signed.url)
