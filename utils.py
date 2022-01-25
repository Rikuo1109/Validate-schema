from __future__ import annotations

import datetime as dt
import functools
import inspect
import re
import typing
from collections.abc import Mapping

from rest_framework.exceptions import APIException

from .base import FieldABC


class _Missing:
    def __bool__(self):
        return False

    def __copy__(self):
        return self

    def __deepcopy__(self, _):
        return self


# Singleton value that indicates that a field's value is missing from input
# dict passed to :meth:`Schema.load`. If the field's value is not required,
# it's ``default`` value is used.
missing = _Missing()


def is_generator(obj) -> bool:
    """Return True if ``obj`` is a generator"""
    return inspect.isgeneratorfunction(obj) or inspect.isgenerator(obj)


def is_iterable_but_not_string(obj) -> bool:
    return (hasattr(obj, "__iter__") and not hasattr(obj, "strip")) or is_generator(obj)


def is_collection(obj) -> bool:
    return is_iterable_but_not_string(obj) and not isinstance(obj, Mapping)


def is_instance_or_subclass(val, class_) -> bool:
    try:
        return issubclass(val, class_)
    except TypeError:
        return isinstance(val, class_)


_iso8601_datetime_re = re.compile(
    r"(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})"
    r"[T ](?P<hour>\d{1,2}):(?P<minute>\d{1,2})"
    r"(?::(?P<second>\d{1,2})(?:\.(?P<microsecond>\d{1,6})\d{0,6})?)?"
    r"(?P<tzinfo>Z|[+-]\d{2}(?::?\d{2})?)?$"
)

_iso8601_date_re = re.compile(
    r"(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})$")

_iso8601_time_re = re.compile(
    r"(?P<hour>\d{1,2}):(?P<minute>\d{1,2})"
    r"(?::(?P<second>\d{1,2})(?:\.(?P<microsecond>\d{1,6})\d{0,6})?)?"
)


def get_fixed_timezone(offset: int | float | dt.timedelta) -> dt.timezone:
    """Return a tzinfo instance with a fixed offset from UTC."""
    if isinstance(offset, dt.timedelta):
        offset = offset.total_seconds() // 60
    sign = "-" if offset < 0 else "+"
    hhmm = "%02d%02d" % divmod(abs(offset), 60)
    name = sign + hhmm
    return dt.timezone(dt.timedelta(minutes=offset), name)


def from_iso_datetime(value):
    """Parse a string and return a datetime.datetime.

    This function supports time zone offsets. When the input contains one,
    the output uses a timezone with a fixed offset from UTC.
    """
    match = _iso8601_datetime_re.match(value)
    if not match:
        raise ValueError("Not a valid ISO8601-formatted datetime string")
    kw = match.groupdict()
    kw["microsecond"] = kw["microsecond"] and kw["microsecond"].ljust(6, "0")
    tzinfo = kw.pop("tzinfo")
    if tzinfo == "Z":
        tzinfo = dt.timezone.utc
    elif tzinfo is not None:
        offset_mins = int(tzinfo[-2:]) if len(tzinfo) > 3 else 0
        offset = 60 * int(tzinfo[1:3]) + offset_mins
        if tzinfo[0] == "-":
            offset = -offset
        tzinfo = get_fixed_timezone(offset)
    kw = {k: int(v) for k, v in kw.items() if v is not None}
    kw["tzinfo"] = tzinfo  # type:ignore
    return dt.datetime(**kw)


def from_iso_time(value):
    """Parse a string and return a datetime.time.

    This function doesn't support time zone offsets.
    """
    match = _iso8601_time_re.match(value)
    if not match:
        raise ValueError("Not a valid ISO8601-formatted time string")
    kw = match.groupdict()
    kw["microsecond"] = kw["microsecond"] and kw["microsecond"].ljust(6, "0")
    kw = {k: int(v) for k, v in kw.items() if v is not None}
    return dt.time(**kw)


def from_iso_date(value):
    """Parse a string and return a datetime.date."""
    match = _iso8601_date_re.match(value)
    if not match:
        raise ValueError("Not a valid ISO8601-formatted date string")
    kw = {k: int(v) for k, v in match.groupdict().items()}
    return dt.date(**kw)


def isoformat(datetime: dt.datetime) -> str:
    """Return the ISO8601-formatted representation of a datetime object.

    :param datetime datetime: The datetime.
    """
    return datetime.isoformat()


def to_iso_time(time: dt.time) -> str:
    return dt.time.isoformat(time)


def to_iso_date(date: dt.date) -> str:
    return dt.date.isoformat(date)


def ensure_text_type(val: str | bytes) -> str:
    if isinstance(val, bytes):
        val = val.decode("utf-8")
    return str(val)


def get_value(obj, key: int | str, default=missing):
    """Helper for pulling a keyed value off various types of objects. Fields use
    this method by default to access attributes of the source object. For object `x`
    and attribute `i`, this method first tries to access `x[i]`, and then falls back to
    `x.i` if an exception is raised.

    .. warning::
        If an object `x` does not raise an exception when `x[i]` does not exist,
        `get_value` will never check the value `x.i`. Consider overriding
        `fields.Field.get_value` in this case.
    """
    if not isinstance(key, int) and "." in key:
        return _get_value_for_keys(obj, key.split("."), default)
    else:
        return _get_value_for_key(obj, key, default)


def _get_value_for_keys(obj, keys, default):
    if len(keys) == 1:
        return _get_value_for_key(obj, keys[0], default)
    else:
        return _get_value_for_keys(
            _get_value_for_key(obj, keys[0], default), keys[1:], default
        )


def _get_value_for_key(obj, key, default):
    if not hasattr(obj, "__getitem__"):
        return getattr(obj, key, default)

    try:
        return obj[key]
    except (KeyError, IndexError, TypeError, AttributeError):
        return getattr(obj, key, default)


def set_value(dct: dict[str, typing.Any], key: str, value: typing.Any):
    """Set a value in a dict. If `key` contains a '.', it is assumed
    be a path (i.e. dot-delimited string) to the value's location.

    ::

        >>> d = {}
        >>> set_value(d, 'foo.bar', 42)
        >>> d
        {'foo': {'bar': 42}}
    """
    if "." in key:
        head, rest = key.split(".", 1)
        target = dct.setdefault(head, {})
        if not isinstance(target, dict):
            raise ValueError(
                "Cannot set {key} in {head} "
                "due to existing value: {target}".format(
                    key=key, head=head, target=target
                )
            )
        set_value(target, rest, value)
    else:
        dct[key] = value


def _signature(func: typing.Callable) -> list[str]:
    return list(inspect.signature(func).parameters.keys())


def get_func_args(func: typing.Callable) -> list[str]:
    """Given a callable, return a list of argument names. Handles
    `functools.partial` objects and class-based callables.

    .. versionchanged:: 3.0.0a1
        Do not return bound arguments, eg. ``self``.
    """
    if inspect.isfunction(func) or inspect.ismethod(func):
        return _signature(func)
    if isinstance(func, functools.partial):
        return _signature(func.func)
    # Callable class
    return _signature(func)


def resolve_field_instance(cls_or_instance):
    """Return a Schema instance from a Schema class or instance.

    :param type|Schema cls_or_instance: Schema class or instance.
    """
    if isinstance(cls_or_instance, type):
        if not issubclass(cls_or_instance, FieldABC):
            print("Class in nested field must be type of FieldABC")
            raise APIException(
                detail='Contact admin for support'
            )
        return cls_or_instance()
    else:
        if not isinstance(cls_or_instance, FieldABC):
            print("Class in nested field must be type of FieldABC")
            raise APIException(
                detail='Contact admin for support'
            )
        return cls_or_instance
