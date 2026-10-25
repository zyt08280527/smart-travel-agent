"""Curated facts only; map spending fields never become ticket prices."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib.resources import files
from math import cos, hypot, radians

from travel_agent.domain.attraction import AttractionLookup, AttractionProfile
from travel_agent.domain.place import Place


def load_profiles() -> list[AttractionProfile]:
    data = files("travel_agent").joinpath("data/attractions_shenzhen.json").read_text("utf-8")
    return [AttractionProfile.model_validate(item) for item in json.loads(data)]


class AttractionService:
    def __init__(self, profiles: Sequence[AttractionProfile] | None = None) -> None:
        self._profiles = list(profiles) if profiles is not None else load_profiles()

    def lookup(self, place: Place, *, now: datetime | None = None) -> AttractionLookup:
        # Names alone can select the wrong campus. Require curated ID, city and
        # normalized WGS84 position. Duplicate bindings also fail closed.
        matches = []
        for profile in self._profiles:
            if (
                not profile.enabled
                or place.city_code != profile.card.city_code
                or place.provider_place_id not in profile.provider_place_ids
            ):
                continue
            distance_m = hypot(
                (place.latitude - profile.latitude) * 111_320,
                (place.longitude - profile.longitude) * 111_320 * cos(radians(profile.latitude)),
            )
            if distance_m <= profile.match_radius_m:
                matches.append(profile)
        if len(matches) != 1:
            return AttractionLookup(status="not_found")
        card = matches[0].card.model_copy(deep=True)
        current = now or datetime.now(UTC)
        if current.utcoffset() is None:
            raise ValueError("核验时间必须包含时区")
        for fact in card.facts:
            if fact.status == "verified" and (
                fact.expires_at <= current
                or any(source.checked_at > current for source in fact.sources)
            ):
                fact.status = "expired"
                fact.value = None
                fact.ticket_price_yuan = None
        return AttractionLookup(status="matched", card=card)
