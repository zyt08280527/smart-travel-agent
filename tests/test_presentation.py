from langchain.messages import AIMessage, HumanMessage, ToolMessage

from travel_agent.presentation import (
    ITINERARY_CAPABILITY_NOTICE,
    LIVE_TRAFFIC_NOTICE,
    TRANSIT_COST_NOTICE,
    TRANSIT_LIVE_NOTICE,
    UV_DATA_NOTICE,
    WEATHER_CAPABILITY_NOTICE,
    render_user_response,
)

ATTRIBUTION = (
    "Data © OpenStreetMap contributors, ODbL 1.0. http://osm.org/copyright"
)


def test_render_user_response_appends_place_attribution() -> None:
    messages = [
        ToolMessage(
            name="search_places",
            tool_call_id="call-1",
            content=(
                '[{"ignored": true}]'
            ),
        ),
        ToolMessage(
            name="search_places",
            tool_call_id="call-2",
            content=f'{{"places": [], "attribution": "{ATTRIBUTION}"}}',
        ),
        AIMessage(content="找到了两个候选地点。"),
    ]

    rendered = render_user_response(messages)

    assert rendered.endswith(f"数据来源：{ATTRIBUTION}")


def test_render_user_response_does_not_duplicate_existing_attribution() -> None:
    messages = [
        ToolMessage(
            name="search_places",
            tool_call_id="call-1",
            content=f'{{"places": [], "attribution": "{ATTRIBUTION}"}}',
        ),
        AIMessage(content=f"找到了候选地点。\n\n数据来源：{ATTRIBUTION}"),
    ]

    rendered = render_user_response(messages)

    assert rendered.count(ATTRIBUTION) == 1


def test_render_user_response_keeps_weather_answer_unchanged() -> None:
    messages = [AIMessage(content="深圳当前天气晴朗。")]

    assert render_user_response(messages) == "深圳当前天气晴朗。"


def test_render_user_response_replaces_unsupported_precise_weather_claim() -> None:
    messages = [
        AIMessage(
            content=(
                "以上是深圳大学的候选地点。\n\n"
                "如需查询其中某个具体校区的实时天气，请告诉我。"
            )
        )
    ]

    rendered = render_user_response(messages)

    assert "查询其中某个具体校区的实时天气" not in rendered
    assert rendered.endswith(WEATHER_CAPABILITY_NOTICE)


def test_render_user_response_keeps_truthful_capability_denial() -> None:
    denial = "当前不支持查询具体校区的天气。"

    assert render_user_response([AIMessage(content=denial)]) == denial


def test_render_user_response_replaces_unsupported_itinerary_export_claim() -> None:
    messages = [
        AIMessage(
            content=(
                "行程已经保存。\n\n"
                "如需查看详细导航步骤、重新规划路线或导出行程，可随时告诉我。"
            )
        )
    ]

    rendered = render_user_response(messages)

    assert "可随时告诉我" not in rendered
    assert rendered.endswith(ITINERARY_CAPABILITY_NOTICE)


def test_render_user_response_keeps_truthful_itinerary_export_denial() -> None:
    denial = "当前支持本地保存行程，暂不支持导出行程。"

    assert render_user_response([AIMessage(content=denial)]) == denial


def test_render_user_response_replaces_unsupported_uv_claim() -> None:
    messages = [
        AIMessage(
            content=(
                "当前天气晴朗，紫外线较强。建议：\n"
                "- 出门做好一般性防晒准备。"
            )
        )
    ]

    rendered = render_user_response(messages)

    assert "紫外线较强" not in rendered
    assert rendered.endswith(UV_DATA_NOTICE)


def test_render_user_response_keeps_truthful_uv_data_boundary() -> None:
    boundary = "天气工具未提供紫外线数据，可以做一般性防晒准备。"

    assert render_user_response([AIMessage(content=boundary)]) == boundary


def test_render_user_response_replaces_unsupported_live_traffic_claim() -> None:
    messages = [
        AIMessage(
            content=(
                "路线预计需要18分钟。\n"
                "- 虽当前无拥堵提示，仍建议出发前查看实时导航。"
            )
        )
    ]

    rendered = render_user_response(messages)

    assert "无拥堵提示" not in rendered
    assert rendered.endswith(LIVE_TRAFFIC_NOTICE)


def test_render_user_response_keeps_truthful_live_traffic_boundary() -> None:
    boundary = "路线工具不提供实时拥堵信息，请在出发前查看实时导航。"

    assert render_user_response([AIMessage(content=boundary)]) == boundary


def test_render_user_response_keeps_provider_backed_driving_traffic_claim() -> None:
    messages = [
        ToolMessage(
            name="plan_driving_route",
            tool_call_id="call-route",
            content=(
                '{"duration_basis":"traffic_aware_estimate",'
                '"traffic_segments":[{"status":"畅通","distance_m":1000}],'
                '"attribution":"驾车路线数据来源：高德地图 Web服务 API"}'
            ),
        ),
        AIMessage(content="查询时大部分路段路况畅通，出发前请再次确认。"),
    ]

    rendered = render_user_response(messages)

    assert "路况畅通" in rendered
    assert LIVE_TRAFFIC_NOTICE not in rendered


def test_render_user_response_appends_route_attribution() -> None:
    route_attribution = "Routing data © OpenStreetMap contributors"
    messages = [
        ToolMessage(
            name="plan_driving_route",
            tool_call_id="call-route",
            content=(
                '{"distance_m": 15842.9, "attribution": '
                f'"{route_attribution}"}}'
            ),
        ),
        AIMessage(content="驾车距离约 15.8 公里。"),
    ]

    rendered = render_user_response(messages)

    assert rendered.endswith(f"数据来源：{route_attribution}")


def test_render_user_response_understands_composite_planning_result() -> None:
    messages = [
        HumanMessage(content="请比较出行方式。"),
        ToolMessage(
            name="recommend_travel_plan",
            tool_call_id="call-plan",
            content=(
                '{"recommendation":{"ranked_options":['
                '{"option":{"mode":"driving",'
                '"duration_basis":"traffic_aware_estimate",'
                '"traffic_status_counts":{"畅通":2},'
                '"attribution":"高德驾车"}},'
                '{"option":{"mode":"transit","cost_yuan":null,'
                '"attribution":"高德公交"}},'
                '{"option":{"mode":"walking",'
                '"attribution":"高德步行"}}]}}'
            ),
        ),
        AIMessage(content="查询时路况畅通。\n公交免费。"),
    ]

    rendered = render_user_response(messages)

    assert "查询时路况畅通" in rendered
    assert LIVE_TRAFFIC_NOTICE not in rendered
    assert "公交免费" not in rendered
    assert TRANSIT_COST_NOTICE in rendered
    assert "高德驾车" in rendered
    assert "高德公交" in rendered
    assert "高德步行" in rendered


def test_render_user_response_deterministically_renders_planning_result() -> None:
    messages = [
        HumanMessage(content="哪种方式合适？"),
        ToolMessage(
            name="recommend_travel_plan",
            tool_call_id="call-plan",
            content=(
                '{"context":{"city":"深圳","origin_name":"粤海校区",'
                '"destination_name":"丽湖校区","weather":{'
                '"temperature_c":30,"apparent_temperature_c":33,'
                '"precipitation_mm":0,"wind_speed_kmh":10,'
                '"condition":"晴朗"}},"recommendation":{'
                '"recommended_mode":"driving","confidence":0.55,'
                '"ranked_options":[{"option":{"mode":"driving",'
                '"distance_m":10000,"duration_s":1800,"tolls_yuan":0,'
                '"taxi_cost_yuan":30,"traffic_status_counts":{"畅通":3},'
                '"attribution":"高德驾车"},"scores":{"total":90}},'
                '{"option":{"mode":"transit","distance_m":11000,'
                '"duration_s":2400,"walking_distance_m":500,'
                '"transfer_count":1,"cost_yuan":null,'
                '"attribution":"高德公交"},"scores":{"total":80}}],'
                '"limitations":["transit 方案缺少费用数据"]}}'
            ),
        ),
        AIMessage(content="无需停车，路线很直接，强烈推荐驾车。"),
    ]

    rendered = render_user_response(messages)

    assert "推荐方式：驾车（推荐区分度 55.0%）" in rendered
    assert "道路通行费 0 元" in rendered
    assert "出租车估价 30 元" in rendered
    assert "票价未提供" in rendered
    assert "公共交通方案缺少费用数据" in rendered
    assert "无需停车" not in rendered
    assert "路线很直接" not in rendered
    assert "强烈推荐" not in rendered
    assert "高德驾车" in rendered
    assert "高德公交" in rendered


def test_render_user_response_uses_only_current_turn_attribution() -> None:
    transit_attribution = "公交路线数据来源：高德地图 Web服务 API"
    walking_attribution = "步行路线数据来源：高德地图 Web服务 API"
    messages = [
        HumanMessage(content="请规划公共交通路线。"),
        ToolMessage(
            name="plan_transit_route",
            tool_call_id="call-transit",
            content=f'{{"attribution": "{transit_attribution}"}}',
        ),
        AIMessage(content="公共交通路线规划完成。"),
        HumanMessage(content="现在改为步行路线。"),
        ToolMessage(
            name="plan_walking_route",
            tool_call_id="call-walking",
            content=f'{{"attribution": "{walking_attribution}"}}',
        ),
        AIMessage(content="步行路线规划完成。"),
    ]

    rendered = render_user_response(messages)

    assert walking_attribution in rendered
    assert transit_attribution not in rendered


def test_render_user_response_replaces_unsupported_free_transit_claim() -> None:
    messages = [
        HumanMessage(content="请规划公共交通路线。"),
        ToolMessage(
            name="plan_transit_route",
            tool_call_id="call-transit",
            content='{"options": [{"cost_yuan": null}]}',
        ),
        AIMessage(content="费用未提供，可能为校内免费通勤车。"),
    ]

    rendered = render_user_response(messages)

    assert "可能为校内免费通勤车" not in rendered
    assert rendered.endswith(TRANSIT_COST_NOTICE)


def test_render_user_response_replaces_unsupported_live_transit_claim() -> None:
    messages = [
        HumanMessage(content="请规划公共交通路线。"),
        ToolMessage(
            name="plan_transit_route",
            tool_call_id="call-transit",
            content='{"options": [{"cost_yuan": 2.0}]}',
        ),
        AIMessage(
            content="建议通过地图 App 确认实时运营状态及实时班次。"
        ),
    ]

    rendered = render_user_response(messages)

    assert "地图 App" not in rendered
    assert rendered.endswith(TRANSIT_LIVE_NOTICE)


def test_render_user_response_replaces_current_transit_status_suggestion() -> None:
    messages = [
        HumanMessage(content="请规划公共交通路线。"),
        ToolMessage(
            name="plan_transit_route",
            tool_call_id="call-transit",
            content='{"options": [{"cost_yuan": null}]}',
        ),
        AIMessage(
            content="建议您出发前通过实时导航确认当前班次与运营状态。"
        ),
    ]

    rendered = render_user_response(messages)

    assert "确认当前班次与运营状态" not in rendered
    assert rendered.endswith(TRANSIT_LIVE_NOTICE)
