import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SearchProfile:
    name: str
    customer_place: str
    min_price: float
    max_pages: int
    keywords: tuple[str, ...]
    required_title_terms: tuple[str, ...]
    excluded_title_terms: tuple[str, ...]


def load_search_profile(profile_name: str | None = None) -> SearchProfile:
    """Loads editable EIS search filters from search_profiles.json."""
    config_path = Path(__file__).with_name("search_profiles.json")
    with config_path.open("r", encoding="utf-8") as config_file:
        config = json.load(config_file)

    selected_name = profile_name or os.getenv("EIS_SEARCH_PROFILE") or config["active_profile"]
    profile = config["profiles"].get(selected_name)
    if profile is None:
        available = ", ".join(config["profiles"])
        raise ValueError(f"Неизвестный профиль поиска {selected_name!r}. Доступны: {available}")

    return SearchProfile(
        name=selected_name,
        customer_place=str(profile["customer_place"]),
        min_price=float(profile["min_price"]),
        max_pages=int(profile["max_pages"]),
        keywords=tuple(profile["keywords"]),
        required_title_terms=tuple(item.casefold() for item in profile["required_title_terms"]),
        excluded_title_terms=tuple(item.casefold() for item in profile["excluded_title_terms"]),
    )


DEFAULT_KEYWORDS = list(load_search_profile().keywords)


def normalize_search_params(
    keywords: list[str] | None,
    max_pages: int | None,
) -> tuple[list[str], int]:
    profile = load_search_profile()
    return (
        list(profile.keywords) if keywords is None else keywords,
        int(os.getenv("EIS_MAX_PAGES", str(profile.max_pages))) if max_pages is None else max_pages,
    )
