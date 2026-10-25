from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from travel_agent.domain.attraction import AttractionProfile
from travel_agent.domain.place import Place
from travel_agent.services.attraction import AttractionService, load_profiles

NOW = datetime(2026, 10, 24, tzinfo=UTC)


def museum_place(**updates):
    return Place.model_validate({
        "display_name": "深圳博物馆金田路馆",
        "latitude": 22.546391063053633,
        "longitude": 114.05700741228037,
        "provider_place_id": "B02F38IRTR",
        "city_code": "440300",
        **updates,
    })


@pytest.mark.parametrize("poi_id", ["B02F38IRTR", "B0FFGM6YKB"])
def test_curated_poi_matches_with_fact_sources_and_conflict(poi_id):
    result = AttractionService().lookup(museum_place(provider_place_id=poi_id), now=NOW)
    assert result.status == "matched"
    assert result.card.attraction_id == "sz-museum-jintian"
    facts = {fact.kind: fact for fact in result.card.facts}
    assert facts["reservation"].status == "verified"
    assert "无需提前预约" in facts["reservation"].value
    assert facts["ticket"].sources
    assert facts["opening"].status == "conflicting"
    assert facts["opening"].value is None
    assert len(facts["opening"].sources) == 2
    assert result.card.suggested_stay_minutes == (120, 240)


@pytest.mark.parametrize("updates", [
    {"provider_place_id": None},
    {"provider_place_id": "other-campus"},
    {"provider_place_id": "B0K2KAYGD3"},  # parking is not a campus
    {"city_code": "440100"},
    {"city_code": None},
    {"latitude": 23.0},
    {"longitude": 115.0},
])
def test_name_alone_wrong_city_parking_and_bad_coordinates_do_not_match(updates):
    assert AttractionService().lookup(museum_place(**updates), now=NOW).model_dump() == {
        "status": "not_found", "card": None,
    }


def test_expiry_boundary_clears_assertions_without_mutating_catalog():
    profiles = load_profiles()
    service = AttractionService(profiles)
    result = service.lookup(museum_place(), now=datetime(2026, 10, 30, 9, 12, tzinfo=UTC))
    assert [fact.status for fact in result.card.facts] == ["expired", "expired", "conflicting"]
    assert all(fact.value is None for fact in result.card.facts)
    assert result.card.facts[0].sources
    assert profiles[0].card.facts[0].status == "verified"
    assert service.lookup(museum_place(), now=NOW).card.facts[0].status == "verified"


def test_not_yet_checked_facts_cannot_be_asserted():
    result = AttractionService().lookup(museum_place(), now=datetime(2026, 10, 22, tzinfo=UTC))
    assert all(fact.value is None for fact in result.card.facts)


def test_disabled_and_duplicate_bindings_fail_closed():
    profiles = load_profiles()
    assert AttractionService([profiles[0], profiles[0]]).lookup(museum_place()).card is None
    profiles[0].enabled = False
    assert AttractionService(profiles).lookup(museum_place()).card is None
    assert AttractionService([]).lookup(museum_place()).card is None


@pytest.mark.parametrize("mutation", [
    {"sources": []}, {"expires_at": None}, {"value": " "},
    {"expires_at": "2026-10-22T00:00:00Z"},
    {"expires_at": "2026-10-30T00:00:00"},
    {"status": "unknown"},
])
def test_invalid_evidence_is_rejected_on_catalog_load(mutation):
    data = load_profiles()[0].model_dump(mode="json")
    data["card"]["facts"][0].update(mutation)
    with pytest.raises(ValidationError):
        AttractionProfile.model_validate(data)


@pytest.mark.parametrize("poi_id,latitude,longitude,expected", [
    ("B0LKCH16U2", 22.54529044, 114.09610982, "sz-museum-tongxin"),
    ("BY0001PJW3", 22.54510850, 114.09644112, "sz-museum-tongxin"),
    ("B02F37WNJN", 22.53806702, 113.91902626, "sz-nanshan-museum"),
])
def test_expanded_campuses_have_independent_facts(poi_id, latitude, longitude, expected):
    result = AttractionService().lookup(museum_place(
        provider_place_id=poi_id, latitude=latitude, longitude=longitude,
    ), now=NOW)
    assert result.card.attraction_id == expected
    assert result.card.category == "museum"
    assert result.card.summary and result.card.summary_source
    facts = {fact.kind: fact for fact in result.card.facts}
    if expected == "sz-nanshan-museum":
        assert facts["ticket"].status == "unknown"
        assert facts["ticket"].value is None
        assert facts["opening"].status == "verified"
    else:
        assert facts["opening"].status == "conflicting"


def test_wrong_campus_coordinates_and_new_parking_ids_do_not_match():
    service = AttractionService()
    for poi_id in ["B0LKCH16U2", "B0H0MZK9WO", "B0FFM3W3WL"]:
        assert service.lookup(museum_place(provider_place_id=poi_id), now=NOW).card is None


def test_every_catalog_binding_is_unique_and_summary_has_source():
    profiles = load_profiles()
    bindings = [(p.card.city_code, poi_id) for p in profiles for poi_id in p.provider_place_ids]
    assert len(bindings) == len(set(bindings))
    data = profiles[0].model_dump(mode="json")
    data["card"]["summary_source"] = None
    with pytest.raises(ValidationError, match="景点简介必须有来源"):
        AttractionProfile.model_validate(data)
