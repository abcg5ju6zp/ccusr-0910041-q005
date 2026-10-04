import types

from importlib.util import module_from_spec, spec_from_file_location
from os import environ as os_environ
from pathlib import Path
from re import findall as re_findall

from sanic.exceptions import LoadFileException, PyFileError
from sanic.helpers import import_string


def str_to_bool(val: str) -> bool:
    """项目内部接口说明。"""

    val = val.lower()
    if val in {
        "y",
        "yes",
        "yep",
        "yup",
        "t",
        "true",
        "on",
        "enable",
        "enabled",
        "1",
    }:
        return True
    elif val in {
        "n",
        "no",
        "f",
        "nope",
        "false",
        "off",
        "disable",
        "disabled",
        "0",
    }:
        return False
    else:
        raise ValueError(f"Invalid truth value {val}")


def load_module_from_file_location(
    location: bytes | str | Path, encoding: str = "utf8", *args, **kwargs
):  # noqa
    """项目内部接口说明。"""
    if isinstance(location, bytes):
        location = location.decode(encoding)

    if isinstance(location, Path) or "/" in location or "$" in location:
        if not isinstance(location, Path):
            # A) Check if location contains any environment variables
            #    in format ${some_env_var}.
            env_vars_in_location = set(re_findall(r"\${(.+?)}", location))

            # B) Check these variables exists in environment.
            not_defined_env_vars = env_vars_in_location.difference(
                os_environ.keys()
            )
            if not_defined_env_vars:
                raise LoadFileException(
                    "The following environment variables are not set: "
                    f"{', '.join(not_defined_env_vars)}"
                )

            # C) Substitute them in location.
            for env_var in env_vars_in_location:
                location = location.replace(
                    "${" + env_var + "}", os_environ[env_var]
                )

        location = str(location)
        if ".py" in location:
            name = location.split("/")[-1].split(".")[
                0
            ]  # get just the file name without path and .py extension
            _mod_spec = spec_from_file_location(
                name, location, *args, **kwargs
            )
            assert _mod_spec is not None  # type assertion for mypy
            module = module_from_spec(_mod_spec)
            _mod_spec.loader.exec_module(module)  # type: ignore

        else:
            module = types.ModuleType("config")
            module.__file__ = str(location)
            try:
                with open(location) as config_file:
                    exec(  # nosec
                        compile(config_file.read(), location, "exec"),
                        module.__dict__,
                    )
            except OSError as e:
                e.strerror = "Unable to load configuration file (e.strerror)"
                raise
            except Exception as e:
                raise PyFileError(location) from e

        return module
    else:
        try:
            return import_string(location)
        except ValueError:
            raise OSError("Unable to load configuration %s" % str(location))
