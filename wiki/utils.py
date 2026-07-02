import re
from typing import Callable, ParamSpec, TypeVar

from langsmith import traceable as _traceable


def normalize_search_query(query: str) -> str:
    return re.sub(r"[?;&|!(){}[\]\-*\\~]", " ", query).strip()


P = ParamSpec("P")
R = TypeVar("R")


def traceable(*dargs, **dkwargs) -> Callable[[Callable[P, R]], Callable[P, R]]:
    return _traceable(*dargs, **dkwargs)
