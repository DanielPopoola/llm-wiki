import re


def normalize_search_query(query: str) -> str:
    return re.sub(r"[?;&|!(){}[\]\-*\\~]", " ", query).strip()
