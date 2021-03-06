"""Validation classes for various types of data."""
from __future__ import annotations

import re
import typing
from abc import ABC, abstractmethod
from itertools import zip_longest
from operator import attrgetter

from . import types
from rest_framework.exceptions import ValidationError

_T = typing.TypeVar("_T")


class Validator(ABC):
    """Abstract base class for validators.

    .. note::
        This class does not provide any validation behavior. It is only used to
        add a useful `__repr__` implementation for validators.
    """

    error = None  # type: str | None

    @abstractmethod
    def __call__(self, value: typing.Any, name: str | None = None) -> typing.Any:
        ...


class And(Validator):
    """Compose multiple validators and combine their error messages.

    Example: ::
        def is_even(value):
            if value % 2 != 0:
                raise ValidationError("Not an even value.")

        validator = validate.And(validate.Range(min=0), is_even)

    :param validators: Validators to combine.
    """

    def __init__(self, *validators: types.Validator):
        self.validators = tuple(validators)

    def __call__(self, value: typing.Any, name: str) -> typing.Any:
        for validator in self.validators:
            r = validator(value, name)
            if not isinstance(validator, Validator) and r is False:
                raise ValidationError(
                    detail='Invalid arguments'
                )
        return value


class URL(Validator):
    """Validate a URL.

    :param relative: Whether to allow relative URLs.
    :param error: Error message to raise in case of a validation error.
        Can be interpolated with `{input}`.
    :param schemes: Valid schemes. By default, ``http``, ``https``,
        ``ftp``, and ``ftps`` are allowed.
    :param require_tld: Whether to reject non-FQDN hostnames.
    """

    class RegexMemoizer:
        def __init__(self):
            self._memoized = {}

        def _regex_generator(self, relative: bool, require_tld: bool) -> typing.Pattern:
            return re.compile(
                r"".join(
                    (
                        r"^",
                        r"(" if relative else r"",
                        # scheme is validated separately
                        r"(?:[a-z0-9\.\-\+]*)://",
                        r"(?:[^:@]+?(:[^:@]*?)?@|)",  # basic auth
                        r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+",
                        r"(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|",  # domain...
                        r"localhost|",  # localhost...
                        (
                            r"(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.?)|"
                            if not require_tld
                            else r""
                        ),  # allow dotless hostnames
                        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|",  # ...or ipv4
                        r"\[[A-F0-9]*:[A-F0-9:]+\])",  # ...or ipv6
                        r"(?::\d+)?",  # optional port
                        r")?"
                        if relative
                        else r"",  # host is optional, allow for relative URLs
                        r"(?:/?|[/?]\S+)\Z",
                    )
                ),
                re.IGNORECASE,
            )

        def __call__(self, relative: bool, require_tld: bool) -> typing.Pattern:
            key = (relative, require_tld)
            if key not in self._memoized:
                self._memoized[key] = self._regex_generator(
                    relative, require_tld)

            return self._memoized[key]

    _regex = RegexMemoizer()

    default_schemes = {"http", "https", "ftp", "ftps"}

    def __init__(
        self,
        *,
        relative: bool = False,
        schemes: types.StrSequenceOrSet | None = None,
        require_tld: bool = True,
    ):
        self.relative = relative
        self.schemes = schemes or self.default_schemes
        self.require_tld = require_tld

    def __call__(self, value: str, name: str) -> str:
        message = f'Field {name} must be a valid url'
        if "://" in value:
            scheme = value.split("://")[0].lower()
            if scheme not in self.schemes:
                raise ValidationError(message)

        regex = self._regex(self.relative, self.require_tld)

        if not regex.search(value):
            raise ValidationError(message)

        return value


class Email(Validator):
    """Validate an email address.

    :param error: Error message to raise in case of a validation error. Can be
        interpolated with `{input}`.
    """

    USER_REGEX = re.compile(
        r"(^[-!#$%&'*+/=?^`{}|~\w]+(\.[-!#$%&'*+/=?^`{}|~\w]+)*\Z"  # dot-atom
        # quoted-string
        r'|^"([\001-\010\013\014\016-\037!#-\[\]-\177]'
        r'|\\[\001-\011\013\014\016-\177])*"\Z)',
        re.IGNORECASE | re.UNICODE,
    )

    DOMAIN_REGEX = re.compile(
        # domain
        r"(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+" r"(?:[A-Z]{2,6}|[A-Z0-9-]{2,})\Z"
        # literal form, ipv4 address (SMTP 4.1.3)
        r"|^\[(25[0-5]|2[0-4]\d|[0-1]?\d?\d)"
        r"(\.(25[0-5]|2[0-4]\d|[0-1]?\d?\d)){3}\]\Z",
        re.IGNORECASE | re.UNICODE,
    )

    DOMAIN_WHITELIST = ("localhost",)

    def __call__(self, value: str, name: str) -> str:
        message = "Field {name} must be an valid email".format(name=name)

        if not value or "@" not in value:
            raise ValidationError(detail=message)

        user_part, domain_part = value.rsplit("@", 1)

        if not self.USER_REGEX.match(user_part):
            raise ValidationError(detail=message)

        if domain_part not in self.DOMAIN_WHITELIST:
            if not self.DOMAIN_REGEX.match(domain_part):
                try:
                    domain_part = domain_part.encode("idna").decode("ascii")
                except UnicodeError:
                    pass
                else:
                    if self.DOMAIN_REGEX.match(domain_part):
                        return value
                raise ValidationError(message)

        return value


class Password(Validator):

    UPPERCASE_PATTERN = '[A-Z]'
    SPECIAL_CHARACTERS = r'[.@_!#$%^&*()<>?/\|}{~:]'

    def __init__(self, *, length_min=8, contain_number: bool = True, cotain_uppercase: bool = False, contain_special: bool = False):
        self.cotain_number = contain_number
        self.cotain_uppercase = cotain_uppercase
        self.cotain_special = contain_special
        self.length_min = length_min

    @classmethod
    def has_only_number(cls, input):
        return input.isdecimal()

    @classmethod
    def has_number(cls, input):
        return any(letter.isnumeric() or letter.isdigit() for letter in input)

    @classmethod
    def has_uppercase_character(cls, input):
        return bool(re.match(cls.UPPERCASE_PATTERN, input))

    @classmethod
    def has_special_character(cls, input):
        regex = re.compile(cls.SPECIAL_CHARACTERS)
        if (regex.search(input)):
            return True
        return False

    def __call__(self, value: str, name: str) -> str:

        errors = []
        value = str(value)

        if len(value) < self.length_min:
            errors.append(f'Field {name} is too short')

        if self.has_only_number(value):
            errors.append(f'Field {name} entirely numeric')

        if self.cotain_number and (not self.has_number(value)):
            errors.append(f'Field {name} must contain number')

        if self.cotain_uppercase and (not self.has_uppercase_character(value)):
            errors.append(f'Field {name} must contain uppercase characters')

        if self.cotain_special and (not self.has_special_character(value)):
            errors.append(
                f'Field {name} must contain special characters'
            )

        if errors:
            raise ValidationError(detail=errors)

        return value


class OneOfEnum(Validator):
    def __init__(self, *, enum_class):
        self.enum_class = enum_class

    def __call__(self, value: str, name: str) -> str | None:
        value = value.upper()

        keys = [v for v in self.enum_class.__members__.keys()]
        if value in keys:
            return self.enum_class[value]
        elif value in [v.value for v in self.enum_class.__members__.values()]:
            return value
        else:
            raise ValidationError(
                detail=f'Field {name} must be one of: {keys}'
            )


class Range(Validator):
    """Validator which succeeds if the value passed to it is within the specified
    range. If ``min`` is not specified, or is specified as `None`,
    no lower bound exists. If ``max`` is not specified, or is specified as `None`,
    no upper bound exists. The inclusivity of the bounds (if they exist) is configurable.
    If ``min_inclusive`` is not specified, or is specified as `True`, then
    the ``min`` bound is included in the range. If ``max_inclusive`` is not specified,
    or is specified as `True`, then the ``max`` bound is included in the range.

    :param min: The minimum value (lower bound). If not provided, minimum
        value will not be checked.
    :param max: The maximum value (upper bound). If not provided, maximum
        value will not be checked.
    :param min_inclusive: Whether the `min` bound is included in the range.
    :param max_inclusive: Whether the `max` bound is included in the range.
    :param error: Error message to raise in case of a validation error.
        Can be interpolated with `{input}`, `{min}` and `{max}`.
    """

    message_min = "Field {{name}} must be {min_op} {{min}}."
    message_max = "Field {{name}} must be {max_op} {{max}}."
    message_all = "Field {{name}} must be {min_op} {{min}} and {max_op} {{max}}."

    message_gte = "greater than or equal to"
    message_gt = "greater than"
    message_lte = "less than or equal to"
    message_lt = "less than"

    def __init__(
        self,
        min=None,
        max=None,
        *,
        min_inclusive: bool = True,
        max_inclusive: bool = True,
        error: str | None = None,
    ):
        self.min = min
        self.max = max
        self.error = error
        self.min_inclusive = min_inclusive
        self.max_inclusive = max_inclusive

        # interpolate messages based on bound inclusivity
        self.message_min = self.message_min.format(
            min_op=self.message_gte if self.min_inclusive else self.message_gt
        )
        self.message_max = self.message_max.format(
            max_op=self.message_lte if self.max_inclusive else self.message_lt
        )
        self.message_all = self.message_all.format(
            min_op=self.message_gte if self.min_inclusive else self.message_gt,
            max_op=self.message_lte if self.max_inclusive else self.message_lt,
        )

    def _format_error(self, value, name: str, message: str) -> str:
        return (self.error or message).format(name=name, input=value, min=self.min, max=self.max)

    def __call__(self, value: _T, name: str) -> _T:
        if self.min is not None and (
            value < self.min if self.min_inclusive else value <= self.min
        ):
            message = self.message_min if self.max is None else self.message_all

            raise ValidationError(
                detail=self._format_error(value, name, message)
            )

        if self.max is not None and (
            value > self.max if self.max_inclusive else value >= self.max
        ):
            message = self.message_max if self.min is None else self.message_all
            raise ValidationError(
                detail=self._format_error(value, name, message)
            )

        return value


class Length(Validator):
    """Validator which succeeds if the value passed to it has a
    length between a minimum and maximum. Uses len(), so it
    can work for strings, lists, or anything with length.

    :param min: The minimum length. If not provided, minimum length
        will not be checked.
    :param max: The maximum length. If not provided, maximum length
        will not be checked.
    :param equal: The exact length. If provided, maximum and minimum
        length will not be checked.
    :param error: Error message to raise in case of a validation error.
        Can be interpolated with `{input}`, `{min}` and `{max}`.
    """

    message_min = "Field {name} must be shorter than minimum length {min}."
    message_max = "Field {name} must be longer than maximum length {max}."
    message_all = "Field {name}'s length must be between {min} and {max}."
    message_equal = "Field {name}'s length must be {equal}."

    def __init__(
        self,
        min: int | None = None,
        max: int | None = None,
        *,
        equal: int | None = None,
        error: str | None = None,
    ):
        if equal is not None and any([min, max]):
            raise ValueError(
                "The `equal` parameter was provided, maximum or "
                "minimum parameter must not be provided."
            )

        self.min = min
        self.max = max
        self.error = error
        self.equal = equal

    def _format_error(self, value: typing.Sized, name: str, message: str) -> str:
        return (self.error or message).format(
            input=value, min=self.min, max=self.max, equal=self.equal, name=name
        )

    def __call__(self, value: typing.Sized, name: str) -> typing.Sized:
        length = len(value)

        if self.equal is not None:
            if length != self.equal:
                raise ValidationError(
                    detail=self._format_error(value, name, self.message_equal)
                )
            return value

        if self.min is not None and length < self.min:
            message = self.message_min if self.max is None else self.message_all
            raise ValidationError(
                detail=self._format_error(value, name, message)
            )

        if self.max is not None and length > self.max:
            message = self.message_max if self.min is None else self.message_all
            raise ValidationError(
                detail=self._format_error(value, name, message)
            )

        return value


class Regexp(Validator):
    """Validator which succeeds if the ``value`` matches ``regex``.

    .. note::

        Uses `re.match`, which searches for a match at the beginning of a string.

    :param regex: The regular expression string to use. Can also be a compiled
        regular expression pattern.
    :param flags: The regexp flags to use, for example re.IGNORECASE. Ignored
        if ``regex`` is not a string.
    :param error: Error message to raise in case of a validation error.
        Can be interpolated with `{input}` and `{regex}`.
    """

    default_message = "Field {name} does not match expected pattern."

    def __init__(
        self,
        regex: str | bytes | typing.Pattern,
        flags: int = 0,
        *,
        error: str | None = None,
    ):
        self.regex = (
            re.compile(regex, flags) if isinstance(
                regex, (str, bytes)) else regex
        )
        self.error = error or self.default_message  # type: str

    def _format_error(self, value: str | bytes, name: str) -> str:
        return self.error.format(input=value, regex=self.regex.pattern, name=name)

    @typing.overload
    def __call__(self, value: str, name: str) -> str:
        ...

    @typing.overload
    def __call__(self, value: bytes, name: str) -> bytes:
        ...

    def __call__(self, value, name: str):
        if self.regex.match(value) is None:
            raise ValidationError(detail=self._format_error(value, name))

        return value


class NoneOf(Validator):
    """Validator which fails if ``value`` is a member of ``iterable``.

    :param iterable: A sequence of invalid values.
    :param error: Error message to raise in case of a validation error. Can be
        interpolated using `{input}` and `{values}`.
    """

    default_message = "Invalid input."

    def __init__(self, iterable: typing.Iterable, *, error: str | None = None):
        self.iterable = iterable
        self.values_text = ", ".join(str(each) for each in self.iterable)
        self.error = error or self.default_message  # type: str

    def _format_error(self, value) -> str:
        return self.error.format(input=value, values=self.values_text)

    def __call__(self, value: typing.Any, name: str) -> typing.Any:
        try:
            if value in self.iterable:
                raise ValidationError(detail=self._format_error(value))
        except TypeError:
            pass

        return value


class OneOf(Validator):
    """Validator which succeeds if ``value`` is a member of ``choices``.

    :param choices: A sequence of valid values.
    :param labels: Optional sequence of labels to pair with the choices.
    :param error: Error message to raise in case of a validation error. Can be
        interpolated with `{input}`, `{choices}` and `{labels}`.
    """

    def __init__(
        self,
        choices: typing.Iterable,
        labels: typing.Iterable[str] | None = None,
    ):
        self.choices = choices
        self.choices_text = ", ".join(str(choice) for choice in self.choices)
        self.labels = labels if labels is not None else []
        self.labels_text = ", ".join(str(label) for label in self.labels)

    def _format_error(self, value, name) -> str:
        return f'Field {name} must be one of: {self.choices_text}'

    def __call__(self, value: typing.Any, name: str) -> typing.Any:
        try:
            if value not in self.choices:
                raise ValidationError(detail=self._format_error(value, name))
        except TypeError as error:
            raise ValidationError(
                detail=self._format_error(value, name)) from error

        return value

    def options(
        self,
        valuegetter: str | typing.Callable[[typing.Any], typing.Any] = str,
    ) -> typing.Iterable[tuple[typing.Any, str]]:
        """Return a generator over the (value, label) pairs, where value
        is a string associated with each choice. This convenience method
        is useful to populate, for instance, a form select field.

        :param valuegetter: Can be a callable or a string. In the former case, it must
            be a one-argument callable which returns the value of a
            choice. In the latter case, the string specifies the name
            of an attribute of the choice objects. Defaults to `str()`
            or `str()`.
        """
        valuegetter = valuegetter if callable(
            valuegetter) else attrgetter(valuegetter)
        pairs = zip_longest(self.choices, self.labels, fillvalue="")

        return ((valuegetter(choice), label) for choice, label in pairs)
