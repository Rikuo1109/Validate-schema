from __future__ import annotations

import copy
import datetime as dt
import numbers
import typing
import uuid

from rest_framework.exceptions import APIException, ParseError, ValidationError

from . import class_registry, types, utils, validate
from .base import FieldABC, SchemaABC
from .utils import missing as missing_
from .utils import resolve_field_instance
from .validate import And

__all__ = [
    "Field",
    "Nested",
    "List",
    "String",
    "UUID",
    "Float",
    "Integer",
    "Boolean",
    "DateTime",
    "Date",
    "Url",
    "Email",
]

_T = typing.TypeVar("_T")


class Field(FieldABC):
    """Basic field from which other fields should extend. It applies no
    formatting by default, and should only be used in cases where
    data does not need to be formatted before being serialized or deserialized.
    On error, the name of the field will be returned.

    :param default: Default deserialization value for the field if the field is not
        found in the input data. May be a value or a callable.
    :param validate: Validator or collection of validators that are called
        during deserialization. Validator takes a field's input value as
        its only parameter and returns a boolean.
    :param required: Raise a :exc:`ValidationError` if the field value
        is not supplied during deserialization.
    :param dict error_messages: Overrides for `Field.default_error_messages`.
    """

    def __init__(
        self,
        *,
        default: typing.Any = missing_,
        validate: None
        | (
            typing.Callable[[typing.Any, str], typing.Any]
            | typing.Iterable[typing.Callable[[typing.Any, str], typing.Any]]
        ) = None,
        required: bool = False,
    ) -> None:

        self.load_default = default

        if validate is None:
            self.validators = []
        elif callable(validate):
            self.validators = [validate]
        elif utils.is_iterable_but_not_string(validate):
            self.validators = list(validate)
        else:
            raise ValueError(
                "The 'validate' parameter must be a callable "
                "or a collection of callables."
            )

        self.required = required

    def __deepcopy__(self, memo):
        return copy.copy(self)

    def get_value(self, obj, attr, accessor=None, default=missing_):
        """Return the value for a given key from an object.

        :param object obj: The object to get the value from.
        :param str attr: The attribute/key in `obj` to get the value from.
        :param callable accessor: A callable used to retrieve the value of `attr` from
            the object `obj`. Defaults to `utils.get_value`.
        """
        accessor_func = accessor or utils.get_value

        return accessor_func(obj, attr, default)

    def _validate(self, value, name):
        self._validate_all(value, name)

    @property
    def _validate_all(self):
        return And(*self.validators)

    def _validate_missing(self, value):
        """Validate missing values. Raise a :exc:`ValidationError` if
        `value` should be considered missing.
        """
        if ((value is missing_) or (value == '') or (value is None)) and self.required:
            if self.load_default:
                return
            raise ParseError(
                detail=f'Field {self.name} is required'
            )

    def deserialize(
        self,
        value: typing.Any,
        attr: str | None = None,
        data: typing.Mapping[str, typing.Any] | typing.Any = None,
        **kwargs,
    ):
        """Deserialize ``value``.

        :param value: The value to deserialize.
        :param attr: The attribute/key in `data` to deserialize.
        :param data: The raw input data passed to `Schema.load`.
        :param kwargs: Field-specific keyword arguments.
`       """
        self._validate_missing(value)
        if ((value is missing_) or (value == '') or (value is None)):
            _miss = self.load_default
            return _miss() if callable(_miss) else _miss
        output = self._deserialize(value, attr, data, **kwargs)
        self._validate(value=output, name=self.name)
        return output

    def _bind_to_schema(self, field_name, schema):
        """Update field with values from its parent schema. Called by
        :meth:`Schema._bind_field <Schema._bind_field>`.

        :param str field_name: Field name set in schema.
        :param Schema|Field schema: Parent object.
        """
        self.parent = self.parent or schema
        self.name = self.name or field_name
        self.root = self.root or (
            self.parent.root if isinstance(
                self.parent, FieldABC) else self.parent
        )

    def _deserialize(
        self,
        value: typing.Any,
        attr: str | None,
        data: typing.Mapping[str, typing.Any] | None,
        **kwargs,
    ):
        """Deserialize value. Concrete :class:`Field` classes should implement this method.

        :param value: The value to be deserialized.
        :param attr: The attribute/key in `data` to be deserialized.
        :param data: The raw input data passed to the `Schema.load`.
        :param kwargs: Field-specific keyword arguments.
        :raise ValidationError: In case of formatting or validation failure.
        :return: The deserialized value.
        """
        return value


class Nested(Field):
    """Allows you to nest a :class:`Schema <Schema>` inside a field.

    :param nested: `Schema` instance, class, class name (string), or callable that returns a `Schema` instance.
    :param many: Whether the field is a collection of objects.
    :param kwargs: The same keyword arguments that :class:`Field` receives.
    """

    def __init__(self, nested: SchemaABC | type | str | typing.Callable[[], SchemaABC], *, many: bool = False, **kwargs,):
        self.nested = nested
        self.many = many
        self._schema = None  # Cached Schema instance
        super().__init__(**kwargs)

    @property
    def schema(self):
        if not self._schema:
            # Inherit context from parent.
            if callable(self.nested) and not isinstance(self.nested, type):
                nested = self.nested()
            else:
                nested = self.nested

            if isinstance(nested, SchemaABC):
                self._schema = copy.copy(nested)
                self._schema._init_fields()
            else:
                if isinstance(nested, type) and issubclass(nested, SchemaABC):
                    schema_class = nested
                elif isinstance(nested, str):
                    schema_class = class_registry.get_class(nested)
                else:
                    raise APIException(
                        detail='Contact admin for support'
                    )
                self._schema = schema_class(many=self.many)
        return self._schema

    def _deserialize(self, value, attr, data, **kwargs):
        many = self.schema.many or self.many
        if many and not utils.is_collection(value):
            raise ValidationError(
                detail=f'Field {self.name} must be iterable'
            )
        return self.schema.load(value)


class List(Field):
    """A list field, composed with another `Field` class or
    instance.

    Example: ::

        numbers = fields.List(fields.Float())

    :param cls_or_instance: A field class or instance.
    :param kwargs: The same keyword arguments that :class:`Field` receives.
    """

    def __init__(self, cls_or_instance: Field | type, **kwargs):
        super().__init__(**kwargs)
        self.inner = resolve_field_instance(cls_or_instance)

    def _bind_to_schema(self, field_name, schema):
        super()._bind_to_schema(field_name, schema)
        self.inner = copy.deepcopy(self.inner)
        self.inner._bind_to_schema(field_name, self)

    def _deserialize(self, value, attr, data, **kwargs) -> list[typing.Any]:
        if not utils.is_collection(value):
            raise ValidationError(
                detail=f'Field {self.name} must be iterable'
            )

        result = []
        for idx, each in enumerate(value):
            try:
                result.append(self.inner.deserialize(each, **kwargs))
            except ValidationError as error:
                raise ValidationError(
                    detail=f'Field {self.name}[{idx + 1}]: {error.detail[0]}'
                ) from error
        return result


class String(Field):
    """A string field.

    :param kwargs: The same keyword arguments that :class:`Field` receives.
    """

    def __init__(self, *, upper_case: bool | None = False, **kwargs):
        super().__init__(**kwargs)
        self.upper_case = upper_case

    def _deserialize(self, value, attr, data, **kwargs) -> typing.Any:
        if not isinstance(value, (str, bytes)):
            raise ValidationError(
                detail=f'Except field {self.name} is a string'
            )
        try:
            value = utils.ensure_text_type(value)
            if (self.upper_case):
                value = value.upper()
            return value

        except UnicodeDecodeError:
            raise ValidationError(
                detail=f'Field {self.name} has some invalid characters'
            )


class UUID(String):
    def _validated(self, value) -> uuid.UUID | None:
        if value is None:
            return None
        try:
            return uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError) as error:
            raise ValidationError(
                detail=f'Expect field {self.name} is an uuid'
            ) from error

    def _deserialize(self, value, attr, data, **kwargs) -> uuid.UUID | None:
        return self._validated(value)


class Float(Field):
    """Base class for number fields.

    :param bool as_string: If `True`, format the serialized value as a string.
    :param kwargs: The same keyword arguments that :class:`Field` receives.
    """

    num_type = float  # type: typing.Type

    def _format_num(self, value) -> typing.Any:
        return self.num_type(value)

    def _validated(self, value: _T) -> _T | None:
        if value is None:
            return None
        try:
            return self._format_num(value)
        except (TypeError, ValueError) as error:
            raise ValidationError(
                detail=f'Except field {self.name} is a number'
            ) from error
        except OverflowError as error:
            raise APIException(
                detail=f'Field {self.name} is too large'
            ) from error

    def _deserialize(self, value: _T, attr, data, **kwargs) -> _T | None:
        return self._validated(value)


class Integer(Float):
    """An integer field."""

    num_type = int

    def _validated(self, value):
        if isinstance(value, numbers.Number) and isinstance(
            value, numbers.Integral
        ):
            return super()._validated(value)
        elif isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                raise ValidationError(
                    detail=f'Except field {self.name} is an integer'
                )
        raise ValidationError(
            detail=f'Field {self.name} must be a valid integer'
        )


class Boolean(Field):
    """A boolean field. """

    truthy = {"T", "TRUE", "Y", "YES", "1", 1, True, }
    falsy = {"F", "FALSE", "N", "NO", "0", 0, 0.0, False, }

    def _deserialize(self, value, attr, data, **kwargs):
        if value is None:
            return None

        if isinstance(value, str):
            value = value.upper()

        if value in self.truthy:
            return True
        elif value in self.falsy:
            return False
        raise ValidationError(
            detail=f'Field {self.name} must be a boolean value'
        )


class DateTime(Field):
    """A formatted datetime string.

    :param format: Either ``"iso"`` (for ISO8601), or a date format string. If `None`, defaults to "iso".
    """

    DESERIALIZATION_FUNCS = {
        "iso": utils.from_iso_datetime,
    }  # type: typing.Dict[str, typing.Callable[[str], typing.Any]]

    DEFAULT_FORMAT = "iso"

    OBJ_TYPE = "datetime"

    def __init__(self, format: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.format = format

    def _bind_to_schema(self, field_name, schema):
        super()._bind_to_schema(field_name, schema)
        self.format = (self.format or self.DEFAULT_FORMAT)

    def _deserialize(self, value, attr, data, **kwargs):
        if not value:
            raise ValidationError(
                detail=f'Field {self.name} is invalid'
            )
        data_format = self.format or self.DEFAULT_FORMAT
        func = self.DESERIALIZATION_FUNCS.get(data_format)
        try:
            if (func):
                return func(value)
            else:
                return self._make_object_from_format(value, format)
        except (TypeError, AttributeError, ValueError):
            raise ValidationError(
                detail=f'Except field {self.name} is a {self.OBJ_TYPE} value'
            )

    @ staticmethod
    def _make_object_from_format(value, data_format):
        return dt.datetime.strptime(value, data_format)


class Date(DateTime):
    """ISO8601-formatted date string.

    :param format: Either ``"iso"`` (for ISO8601) or a date format string. If `None`, defaults to "iso".
    """

    DESERIALIZATION_FUNCS = {
        "iso": utils.from_iso_date,
        "iso8601": utils.from_iso_date
    }

    DEFAULT_FORMAT = "iso"

    OBJ_TYPE = "date"

    @ staticmethod
    def _make_object_from_format(value, data_format):
        return dt.datetime.strptime(value, data_format).date()


class Url(String):
    """An URL field.

    :param default: Default value for the field if the attribute is not set.
    :param require_tld: Whether to reject non-FQDN hostnames.
    :param schemes: Valid schemes. By default, ``http``, ``https``,
        ``ftp``, and ``ftps`` are allowed.
    :param kwargs: The same keyword arguments that :class:`String` receives.
    """

    def __init__(self, *, schemes: types.StrSequenceOrSet | None = None, **kwargs,):
        super().__init__(**kwargs)

        validator = validate.URL(
            relative=False,
            schemes=schemes,
            require_tld=True,
        )
        self.validators.insert(0, validator)


class Email(String):
    """An email field."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Insert validation into self.validators so that multiple errors can be stored.
        validator = validate.Email()
        self.validators.insert(0, validator)


class Password(String):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        validator = validate.Password()
        self.validators.insert(0, validator)
