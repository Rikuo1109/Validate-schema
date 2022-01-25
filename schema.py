"""The :class:`Schema` class, including its metaclass and options (class Meta)."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
import copy
import inspect
import typing

from . import base, fields as ma_fields, class_registry, types
from rest_framework.exceptions import ValidationError

from .utils import (
    missing,
    set_value,
    is_collection,
    is_instance_or_subclass,
)


VALIDATES = 'validates'
_T = typing.TypeVar("_T")


def _get_fields(attrs):
    """Get fields from a class. If ordered=True, fields will sorted by creation index.

    :param attrs: Mapping of class attributes
    """
    return [
        (field_name, field_value)
        for field_name, field_value in attrs.items()
        if is_instance_or_subclass(field_value, base.FieldABC)
    ]


# This function allows Schemas to inherit from non-Schema classes and ensures
#   inheritance according to the MRO
def _get_fields_by_mro(klass):
    """Collect fields from a class, following its method resolution order. The
    class itself is excluded from the search; only its parents are checked. Get
    fields from ``_declared_fields`` if available, else use ``__dict__``.

    :param type klass: Class whose fields to retrieve
    """
    mro = inspect.getmro(klass)
    # Loop over mro in reverse to maintain correct order of fields
    return sum(
        (
            _get_fields(
                getattr(base, "_declared_fields", base.__dict__),
            )
            for base in mro[:0:-1]
        ),
        [],
    )


class SchemaMeta(type):
    """Metaclass for the Schema class. Binds the declared fields to
    a ``_declared_fields`` attribute, which is a dictionary mapping attribute
    names to field objects. Also sets the ``opts`` class attribute, which is
    the Schema class's ``class Meta`` options.
    """

    _declared_fields = {}

    def __new__(cls, name, bases, attrs):
        # meta = attrs.get("Meta")

        cls_fields = _get_fields(attrs)
        # Remove fields from list of class attributes to avoid shadowing
        # Schema attributes/methods in case of name conflict
        for field_name, _ in cls_fields:
            del attrs[field_name]
        klass = super().__new__(cls, name, bases, attrs)
        inherited_fields = _get_fields_by_mro(klass)

        # Assign _declared_fields on class
        klass._declared_fields = dict(inherited_fields + cls_fields)

        return klass

    def __init__(cls, name, bases, attrs):
        super().__init__(name, bases, attrs)
        if name:
            class_registry.register(name, cls)
        cls._hooks = cls.resolve_hooks()

    def resolve_hooks(cls) -> dict[types.Tag, list[str]]:
        """Add in the decorated processors

        By doing this after constructing the class, we let standard inheritance
        do all the hard work.
        """
        mro = inspect.getmro(cls)

        # type: typing.Dict[types.Tag, typing.List[str]]
        hooks = defaultdict(list)

        for attr_name in dir(cls):
            # Need to look up the actual descriptor, not whatever might be
            # bound to the class. This needs to come from the __dict__ of the
            # declaring class.
            for parent in mro:
                try:
                    parent.__dict__[attr_name]
                except KeyError:
                    continue
                else:
                    break
            else:
                # In case we didn't find the attribute and didn't break above.
                # We should never hit this - it's just here for completeness
                # to exclude the possibility of attr being undefined.
                continue

        return hooks


class Schema(base.SchemaABC, metaclass=SchemaMeta):  # type:ignore
    """Base schema class with which to define custom schemas.

    :param many: Should be set to `True` if ``obj`` is a collection
        so that the object will be serialized to a list.
     """

    error_messages = {}  # type: typing.Dict[str, str]

    _default_error_messages = {
        "type": "Invalid input type.",
    }  # type: typing.Dict[str, str]

    _declared_fields = {}  # type: typing.Dict[str, ma_fields.Field]
    _hooks = {}  # type: typing.Dict[types.Tag, typing.List[str]]

    class Meta:
        """Options object for a Schema.

        Example usage: ::

            class Meta:
                fields = ("id", "email", "date_created")
                exclude = ("password", "secret_attribute")

        Available options:
        """

    def __init__(self, *, many: bool = False):
        # Raise error if only or exclude is passed as string, not list of strings
        self.declared_fields = copy.deepcopy(self._declared_fields)
        self.many = many
        #: Dictionary mapping field_names -> :class:`Field` objects
        self.load_fields = {}  # type: typing.Dict[str, ma_fields.Field]
        self._init_fields()
        messages = {}
        messages.update(self._default_error_messages)
        for cls in reversed(self.__class__.__mro__):
            messages.update(getattr(cls, "error_messages", {}))
        messages.update(self.error_messages or {})
        self.error_messages = messages

    def _deserialize(
        self,
        data: types.MapingOrIterableMapping,
        *,
        many: bool = False,
        index=None,
    ):
        """Deserialize ``data``.

        :param dict data: The data to deserialize.
        :param ErrorStore error_store: Structure to store errors.
        :param bool many: `True` if ``data`` should be deserialized as a collection.
        :param int index: Index of the item being serialized (for storing errors) if
            serializing a collection, otherwise `None`.
        :return: A dictionary of the deserialized data.
        """
        if many:
            if not is_collection(data):
                raise ValidationError(
                    detail='Except data is an iterable value'
                )
            else:
                ret_l = [
                    typing.cast(
                        _T,
                        self._deserialize(
                            typing.cast(typing.Mapping[str, typing.Any], d),
                            many=False,
                            index=idx,
                        ),
                    )
                    for idx, d in enumerate(data)
                ]
            return ret_l
        ret_d = dict()
        # Check data is a dict
        if not isinstance(data, Mapping):
            raise ValidationError(detail='Except data is an objects')
        else:
            for field_name, field_obj in self.load_fields.items():
                value = field_obj.deserialize(
                    data.get(field_name, missing),  # type:ignore
                    field_name,
                    data
                )

                if value is not missing:
                    set_value(ret_d, field_name, value)
        return ret_d

    def load(self, data: types.MapingOrIterableMapping, *, many: bool | None = None,):
        """Deserialize a data structure to an object defined by this Schema's fields.

        :param data: The data to deserialize.
        :param many: Whether to deserialize `data` as a collection. If `None`, the
            value for `self.many` is used.
        :return: Deserialized data
        """
        return self._deserialize(
            data=data,
            many=self.many if many is None else bool(many),
        )

    def _init_fields(self) -> None:
        fields_dict = dict()
        for field_name in self.declared_fields.keys():
            field_obj = self.declared_fields.get(field_name, ma_fields.Field())
            field_obj._bind_to_schema(field_name, self)
            fields_dict[field_name] = field_obj
        self.load_fields = dict(fields_dict.items())


BaseSchema = Schema  # for backwards compatibility
