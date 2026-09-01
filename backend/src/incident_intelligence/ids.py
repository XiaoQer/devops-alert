from typing import Literal
from uuid import uuid4

IdPrefix = Literal[
    "sig",
    "alt",
    "inc",
    "diag",
    "aud",
    "svc",
    "dep",
    "cjob",
    "cdec",
    "iact",
    "iop",
    "src",
    "acr",
    "rcp",
    "aso",
    "agr",
    "agj",
    "gcj",
    "gdec",
    "amd",
    "aeo",
    "alj",
    "irl",
    "ird",
    "iro",
    "iej",
    "ino",
    "inr",
    "ift",
    "fer",
]


def new_id(prefix: IdPrefix) -> str:
    return f"{prefix}_{uuid4().hex}"
