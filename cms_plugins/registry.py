from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable

from django.core.exceptions import ImproperlyConfigured

from . import CMS_PLUGIN_API_VERSION

Validator = Callable[[dict[str, Any]], dict[str, Any]]
ChoicesProvider = Callable[[], list[dict[str, str]]]
ContextProvider = Callable[[Any, dict[str, Any]], dict[str, Any]]
PlainTextProvider = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class EditorField:
    key: str
    label: str
    type: str = "text"
    value: Any = ""
    options: tuple[Any, ...] = ()
    choices_provider: ChoicesProvider | None = None

    def as_dict(self) -> dict[str, Any]:
        result = {"key": self.key, "label": self.label, "type": self.type, "value": self.value}
        if self.choices_provider is not None:
            result["options"] = self.choices_provider()
        elif self.options:
            result["options"] = list(self.options)
        return result


@dataclass(frozen=True)
class PluginBlock:
    name: str
    label: str
    validate: Validator
    template_name: str
    editor_fields: tuple[EditorField, ...]
    context_provider: ContextProvider | None = None
    plain_text_provider: PlainTextProvider | None = None


@dataclass(frozen=True)
class PluginDefinition:
    key: str
    name: str
    version: str
    description: str
    blocks: tuple[PluginBlock, ...] = ()
    api_version: int = CMS_PLUGIN_API_VERSION
    urlconf: str = ""
    url_prefix: str = ""


_definitions: dict[str, PluginDefinition] = {}
_blocks: dict[str, tuple[str, PluginBlock]] = {}
_PLUGIN_KEY = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_URL_PREFIX = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def register_plugin(definition: PluginDefinition) -> None:
    if definition.api_version != CMS_PLUGIN_API_VERSION:
        raise ImproperlyConfigured(
            f"{definition.key}: CMS plugin API {definition.api_version} is unsupported"
        )
    if not _PLUGIN_KEY.fullmatch(definition.key) or definition.key in _definitions:
        raise ImproperlyConfigured(f"duplicate or empty plugin key: {definition.key!r}")
    if definition.urlconf and not _URL_PREFIX.fullmatch(definition.url_prefix):
        raise ImproperlyConfigured(
            f"{definition.key}: URL prefix must be a non-empty lowercase path segment"
        )
    for block in definition.blocks:
        if not block.name.startswith(definition.key + "."):
            raise ImproperlyConfigured(
                f"{definition.key}: block names must start with '{definition.key}.'"
            )
        if block.name in _blocks:
            raise ImproperlyConfigured(f"duplicate plugin block: {block.name}")
    _definitions[definition.key] = definition
    for block in definition.blocks:
        _blocks[block.name] = (definition.key, block)


def definitions() -> tuple[PluginDefinition, ...]:
    return tuple(_definitions.values())


def plugin_block(name: str) -> tuple[str, PluginBlock] | None:
    return _blocks.get(name)


def clear_registry() -> None:
    """テスト専用。実運用中にレジストリを書き換えない。"""
    _definitions.clear()
    _blocks.clear()
