"""Hand-built replies of the shapes the two services document (ThePornDB's OpenAPI document v3.24.757, stash-box's
schema.graphql), with NEUTRAL SYNTHETIC content: invented studios, titles and performers, example.com addresses, no
real record and no real image. Used by the client, dialog and Redact step tests. Nothing here was recorded from a
live authenticated call (none is possible without a key); the shapes follow the published schemas."""

from __future__ import annotations

import io
import json
import urllib.error

KEY = "SECRET-KEY-do-not-leak-0123456789"
OSHASH = "0123456789abcdef"
MD5 = "0123456789abcdef0123456789abcdef"


def tpdb_scene(**over) -> dict:
    scene = {
        "id": "11111111-2222-3333-4444-555555555555", "_id": 7, "type": "Scene",
        "title": "Test Scene Title", "slug": "example-studio-test-scene-title", "external_id": "ext-1",
        "description": "A synthetic description.\nSecond line.", "date": "2021-11-15",
        "url": "https://example.com/scene/test-scene-title",
        "image": "https://cdn.example.com/test-scene-title/image.jpg", "poster": "https://cdn.example.com/test-scene-title/poster.jpg",
        "posters": {"full": "https://cdn.example.com/p/full.jpg", "large": "https://cdn.example.com/p/large.jpg",
                    "medium": None, "small": None},
        "duration": 1830, "sku": "SKU-0001",
        "site": {"id": 5, "name": "Example Studio", "parent": {"id": 2, "name": "Example Network"}},
        "performers": [{"id": "p1", "name": "Performer One"}, {"id": "p2", "name": "Performer Two"}],
        "tags": [{"id": 1, "name": "Tag A"}, {"id": 2, "name": "Tag B"}],
        "directors": [{"id": 3, "name": "Director One"}],
        "hashes": [{"hash": OSHASH, "type": "OSHASH"}],
    }
    scene.update(over)
    return scene


def stash_scene(**over) -> dict:
    scene = {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "title": "Test Scene Title",
        "details": "A synthetic description.", "release_date": "2021-11-15",
        "urls": [{"url": "https://example.com/scene/test-scene-title"}],
        "studio": {"name": "Example Studio", "parent": {"name": "Example Network"}},
        "performers": [
            {"as": "Credited Alias", "performer": {"name": "Performer One", "disambiguation": "I"}},
            {"as": None, "performer": {"name": "Performer Two", "disambiguation": None}},
        ],
        "tags": [{"name": "Tag A"}, {"name": "Tag B"}],
        "images": [{"url": "https://cdn.example.com/s/one.jpg", "width": 1920, "height": 1080},
                   {"url": "https://cdn.example.com/s/two.jpg", "width": 640, "height": 360}],
        "duration": 1830, "director": "Director One", "code": "CODE-1",
    }
    scene.update(over)
    return scene


def http_error(code: int, body: bytes = b"", url: str = "https://example.com/x") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, {401: "Unauthorized", 404: "Not Found", 429: "Too Many Requests"}.get(code, "Error"),
                                  {}, io.BytesIO(body))


class FakeFetch:
    """A scripted `_fetch`: each call takes the next reply (the last one repeats). A reply is a dict/list (sent as
    JSON), bytes, or an exception instance (raised). Every request is kept in `.requests`."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, BaseException):
            raise reply
        return reply if isinstance(reply, bytes) else json.dumps(reply).encode("utf-8")

    def body(self, index: int = -1) -> dict:
        return json.loads(self.requests[index].data.decode("utf-8"))

    @staticmethod
    def header(request, name: str) -> str:
        return request.get_header(name.capitalize()) or ""
