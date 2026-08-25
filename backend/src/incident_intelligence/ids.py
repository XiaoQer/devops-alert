from typing import Literal
from uuid import uuid4

IdPrefix = Literal["sig", "alt", "inc", "diag", "aud", "svc", "dep", "cjob", "cdec"]


def new_id(prefix: IdPrefix) -> str:
    return f"{prefix}_{uuid4().hex}"
