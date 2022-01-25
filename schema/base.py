from __future__ import annotations


class FieldABC:
    """Abstract base class from which all Field classes inherit."""

    parent = None
    name = None
    root = None

    def deserialize(self, value):
        raise NotImplementedError

    def _deserialize(self, value, attr, data, **kwargs):
        raise NotImplementedError

    def _bind_to_schema(self, field_name, schema):
        raise NotImplementedError


class SchemaABC:
    """Abstract base class from which all Schemas inherit."""

    many = None
    _declared_fields = {}

    def __init__(self, many: bool | None = None):
        raise NotImplementedError

    def load(self, data, *, many: bool | None = None):
        raise NotImplementedError

    def _init_fields(self):
        raise NotImplementedError
