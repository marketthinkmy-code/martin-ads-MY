"""Command orchestrators invoked by ``python -m adbot <command>``.

Client factories live here so each command only constructs the clients it needs
(e.g. ``monitor`` never builds the Drive/LLM clients).
"""

from __future__ import annotations

from ..settings import Settings


def graph_client(settings: Settings):
    from ..clients.graph import GraphClient
    return GraphClient(settings.secrets.meta_token, settings.secrets.meta_app_secret)


def drive_client(settings: Settings):
    from ..clients.drive import DriveClient
    return DriveClient(settings.secrets.google_sa_json)


def docs_client(settings: Settings):
    from ..clients.gdoc import DocsClient
    return DocsClient(settings.secrets.google_sa_json)


def llm_client(settings: Settings):
    from ..clients.llm import LLMClient
    return LLMClient(settings.secrets.anthropic_api_key)
