from __future__ import annotations

import typing
from rest_framework.exceptions import APIException

if typing.TYPE_CHECKING:
    from . import Schema

    SchemaType = typing.Type[Schema]

# {
#   <class_name>: <list of class objects>
#   <module_path_to_class>: <list of class objects>
# }
_registry = {}  # type: dict[str, list[SchemaType]]


def register(classname: str, cls: typing.Any) -> None:
    """Add a class to the registry of serializer classes. When a class is
    registered, an entry for both its classname and its full, module-qualified
    path are added to the registry.

    Example: ::

        class MyClass:
            pass

        register('MyClass', MyClass)
        # Registry:
        # {
        #   'MyClass': [path.to.MyClass],
        #   'path.to.MyClass': [path.to.MyClass],
        # }

    """
    # Module where the class is located
    module = cls.__module__
    # Full module path to the class
    # e.g. user.schemas.UserSchema
    fullpath = ".".join([module, classname])
    # If the class is already registered; need to check if the entries are
    # in the same module as cls to avoid having multiple instances of the same
    # class in the registry
    if classname in _registry and not any(
        each.__module__ == module for each in _registry[classname]
    ):
        _registry[classname].append(cls)
    elif classname not in _registry:
        _registry[classname] = [cls]

    # Also register the full path
    if fullpath not in _registry:
        _registry.setdefault(fullpath, []).append(cls)
    else:
        # If fullpath does exist, replace existing entry
        _registry[fullpath] = [cls]
    return None


def get_class(classname: str) -> SchemaType:
    try:
        classes = _registry[classname]
    except KeyError as error:
        print("Class with name {!r} was not found".format(classname))
        raise APIException(
            detail='Contact admin for support'
        ) from error
    if len(classes) > 1:
        print("Multiple classes with name {!r} were found.".format(classname))
        raise APIException(
            detail='Contact admin for support'
        )
    else:
        return _registry[classname][0]
