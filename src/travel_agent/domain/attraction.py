from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, model_validator


class AttractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class AttractionSource(AttractionModel):
    title: str = Field(min_length=1)
    url: HttpUrl
    checked_at: AwareDatetime


class AttractionFact(AttractionModel):
    kind: Literal["reservation", "ticket", "opening"]
    status: Literal["verified", "unknown", "expired", "conflicting"]
    value: str | None = None
    scope: str = Field(min_length=1)
    sources: list[AttractionSource] = Field(default_factory=list)
    expires_at: AwareDatetime | None = None
    ticket_price_yuan: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_evidence(self) -> "AttractionFact":
        if self.ticket_price_yuan is not None and (
            self.kind != "ticket" or self.status != "verified"
        ):
            raise ValueError("票价数值只能属于已核验的门票事实")
        if self.status == "verified":
            if not self.value or not self.sources or self.expires_at is None:
                raise ValueError("已核验事实必须具有内容、来源与复核期限")
            if any(source.checked_at >= self.expires_at for source in self.sources):
                raise ValueError("复核期限必须晚于来源查阅时间")
        elif self.value is not None:
            raise ValueError("未知、冲突和过期事实不能保留确定性结论")
        return self


class AttractionCard(AttractionModel):
    attraction_id: str
    name: str
    city_code: str
    official_url: HttpUrl
    category: Literal["museum", "park", "gallery"] | None = None
    summary: str | None = Field(default=None, min_length=1, max_length=500)
    summary_source: AttractionSource | None = None
    facts: list[AttractionFact]
    suggested_stay_minutes: tuple[int, int] | None = None
    stay_note: str = "产品建议，按兴趣调整；不会自动修改行程停留时间。"

    @model_validator(mode="after")
    def validate_card(self) -> "AttractionCard":
        if self.summary and self.summary_source is None:
            raise ValueError("景点简介必须有来源")
        if len(self.facts) != 3 or {fact.kind for fact in self.facts} != {
            "reservation", "ticket", "opening"
        }:
            raise ValueError("景点卡必须分别包含一项预约、票价与开放信息")
        if self.suggested_stay_minutes is not None:
            lower, upper = self.suggested_stay_minutes
            if not 0 < lower <= upper <= 1440:
                raise ValueError("建议停留时长范围无效")
        return self


class AttractionProfile(AttractionModel):
    card: AttractionCard
    provider: Literal["amap"] = "amap"
    provider_place_ids: list[str] = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    match_radius_m: float = Field(default=150, gt=0, le=500)
    enabled: bool = True


class AttractionLookup(AttractionModel):
    status: Literal["matched", "not_found"]
    card: AttractionCard | None = None
