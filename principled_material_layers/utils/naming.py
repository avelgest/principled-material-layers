# SPDX-License-Identifier: GPL-2.0-or-later

import itertools as it
import string

from collections.abc import Container
from random import randint
from typing import Callable, Optional


def cap_enum(enum_str: str) -> str:
    """Converts an enum string from all caps to a capitalized string
    with spaces, e.g. 'SOFT_LIGHT' -> "Soft Light".
    """
    return string.capwords(enum_str.replace("_", " "))


def unique_name_in(container: Container,
                   num_bytes: int = 4,
                   attr: Optional[str] = None,
                   format_str: Optional[str] = None) -> str:
    """Generates a random hexadecimal string that is not
    contained by 'container'.
    Params:
        container: A container that the returned name will be unique in.
        num_bytes: The size of the integer to use for generating the
                   random hex string. The length of the hex string will
                   be twice this value. Must be > 1.
        attr: If specified the name of an attribute used to determine
              uniqueness in the object rather than just using the
              container's __contains__ method.
        format_str: If specified formats the name before checking
                    uniqueness. Should be a string containing {} or {0}.
    Returns:
        A string unique in 'container'.
    """

    if num_bytes <= 1:
        raise ValueError("num_bytes must be a positive integer greater than 1")

    if format_str is not None:
        # Test the formatting string
        rand_str = str(randint(0, 2**32))
        if rand_str not in format_str.format(rand_str):
            raise ValueError("format_str is invalid")

    str_len = 2*num_bytes

    for _ in range(100):
        name = f"{randint(1, 2**(8*num_bytes)):0{str_len}X}"

        if format_str is not None:
            name = format_str.format(name)

        if attr is None:
            if name not in container:
                return name
        else:
            if not [x for x in container if getattr(x, attr) == name]:
                return name
    raise RuntimeError("Unable to create unique name")


def unique_name(condition: Callable[[str], bool],
                num_bytes: int = 4,
                format_str: Optional[str] = None) -> str:
    """Generates a unique name for which condition returns True"""

    if num_bytes <= 0:
        raise ValueError("num_bytes must be positive")

    str_len = 2*num_bytes

    for _ in range(100):
        name = f"{randint(1, 2**(8*num_bytes)):0{str_len}X}"
        if format_str is not None:
            name = format_str.format(name)

        if condition(name):
            return name

    raise RuntimeError("Unable to create unique name")


def suffix_num_unique_in(basename: str,
                         container: Container,
                         suffix_len: int = 2) -> str:
    """Incrementally suffix a number to basename so that it is unique
    in container. If container does not contain basename then returns
    basename unaltered, otherwise a suffixed string (e.g. basename.01,
    basename.02 etc) is return.

    Params:
        basename: The string to suffix a number to.
        container: A container that the return value will be unique in.
        suffix_len: The minimum length of the suffix string.
    Returns:
        A string starting with basename that is unique in container.
    """

    if basename not in container:
        return basename

    suffix_num = it.count(1)
    while True:
        name = f"{basename}.{next(suffix_num):0{suffix_len}}"
        if name not in container:
            return name
