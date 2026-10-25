"""Opt-in, persistent one-shot departure checks for immutable saved journeys."""

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from travel_agent.domain.multi_stop import MultiStopDraft, SavedMultiStopJourney
from travel_agent.services.single_departure_watch import SingleWatchSnapshot


class DepartureWatch:
    def __init__(self, workspace, planner, weather, single_checker=None):
        self.workspace, self.planner, self.weather = workspace, planner, weather
        self.single_checker = single_checker

    async def enable_single(self, saved, departure_at=None, *, now=None):
        now = now or datetime.now(UTC)
        context = saved.watch_context
        if not context or not context.origin or not context.destination:
            raise ValueError("旧记录缺少完整规划坐标，请重新规划并保存后开启守护")
        original = context.departure_time
        departure = original.departure_at if original else departure_at
        if original and departure_at is not None and departure_at != original.departure_at:
            raise ValueError("不能覆盖原出发时间，请重新规划保存")
        if departure is None or departure.utcoffset() is None or departure <= now:
            raise ValueError("请补充未来的明确出发时间")
        if original and original.precision not in {"now", "exact"}:
            raise ValueError("请重新规划并确认明确出发时间")
        snapshot = SingleWatchSnapshot(saved=saved, departure_at=departure)
        return await asyncio.to_thread(self._enable_single, snapshot, now)

    def _enable_single(self, snapshot, now):
        key = "single:" + snapshot.saved.itinerary_id
        stamp = snapshot.departure_at.timestamp()
        with self._db() as db:
            self._schema(db)
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO departure_watches
                (id,saved_id,snapshot,departure,due,status) VALUES (?,?,?,?,?,'pending')
                ON CONFLICT(saved_id) DO NOTHING""",
                (
                    str(uuid4()),
                    key,
                    snapshot.model_dump_json(),
                    stamp,
                    max(now.timestamp(), stamp - 1800),
                ),
            )
        return next(row for row in self._list() if row["saved_id"] == key)

    async def cancel_saved_single(self, saved_id):
        await asyncio.to_thread(self._cancel_saved_single, "single:" + saved_id)

    def _cancel_saved_single(self, key):
        if not self.workspace.path.is_file():
            return
        with self._db() as db:
            self._schema(db)
            db.execute("UPDATE departure_watches SET status='cancelled' WHERE saved_id=?", (key,))

    def _db(self):
        return self.workspace._database()

    @staticmethod
    def _schema(db):
        db.execute("""CREATE TABLE IF NOT EXISTS departure_watches (
            id TEXT PRIMARY KEY, saved_id TEXT UNIQUE NOT NULL, snapshot TEXT NOT NULL,
            departure REAL NOT NULL, due REAL NOT NULL, status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, lease REAL NOT NULL DEFAULT 0,
            checked_at TEXT, message TEXT NOT NULL DEFAULT '')""")
        db.execute("""CREATE TABLE IF NOT EXISTS departure_watch_reads
            (id TEXT PRIMARY KEY, token TEXT NOT NULL)""")

    @staticmethod
    def _token(checked_at, message):
        return hashlib.sha256(f"{checked_at}|{message}".encode()).hexdigest() if message else ""

    async def mark_read(self, identity, token):
        await asyncio.to_thread(self._mark_read, str(identity), token)

    def _mark_read(self, identity, token):
        with self._db() as db:
            self._schema(db)
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT checked_at,message FROM departure_watches WHERE id=?", (identity,)
            ).fetchone()
            if row is None:
                raise LookupError("提醒不存在")
            if not token or self._token(*row) != token:
                raise ValueError("提醒已有新结果，请刷新后再标记已读")
            db.execute(
                "INSERT OR REPLACE INTO departure_watch_reads VALUES (?,?)", (identity, token)
            )

    async def detail(self, identity):
        return await asyncio.to_thread(self._detail, str(identity))

    def _detail(self, identity):
        with self._db() as db:
            self._schema(db)
            row = db.execute(
                "SELECT snapshot,departure FROM departure_watches WHERE id=?", (identity,)
            ).fetchone()
            if row is None:
                raise LookupError("提醒不存在")
            payload = json.loads(row[0])
            if payload.get("kind") == "single":
                snapshot = SingleWatchSnapshot.model_validate(payload)
                source = snapshot.saved.model_dump(mode="json")
                kind = "single"
                context = snapshot.saved.watch_context
                route = f"{snapshot.saved.origin} → {snapshot.saved.destination}"
                preferences = context.preferences
                deadline = context.arrival_deadline
                extra = f"原选交通方式：{snapshot.saved.travel_mode}。"
                departure = snapshot.departure_at.isoformat()
            else:
                snapshot = SavedMultiStopJourney.model_validate(payload)
                source = snapshot.model_dump(mode="json")
                kind = "multi"
                request = snapshot.draft.result.request
                route = " → ".join(f"{s.name}（停留{s.stay_minutes:g}分钟）" for s in request.stops)
                preferences, deadline = request.preferences, request.arrival_deadline
                extra = "".join(
                    f"{s.name}到达时间窗：{s.earliest_arrival_at}至{s.latest_arrival_at}。"
                    for s in request.stops
                    if s.earliest_arrival_at or s.latest_arrival_at
                )
                if request.max_transport_cost_yuan is not None:
                    extra += f"交通预算不超过{request.max_transport_cost_yuan:g}元。"
                if request.max_elapsed_minutes is not None:
                    extra += f"全程不超过{request.max_elapsed_minutes:g}分钟。"
                departure = request.departure_time.departure_at.isoformat()
            priorities = {
                "balanced": "综合均衡",
                "fastest": "最快",
                "cheapest": "最省钱",
                "least_walking": "最少步行",
                "fewest_transfers": "最少换乘",
                "subway_first": "地铁优先",
                "recommended": "推荐策略",
            }
            extra += (f"偏好：{priorities[preferences.priority]}，"
                      f"公交{priorities[preferences.transit_strategy]}。")
            if preferences.can_drive is not None:
                extra += "可以驾车。" if preferences.can_drive else "不能驾车。"
            if preferences.max_walking_distance_m is not None:
                extra += f"步行不超过{preferences.max_walking_distance_m:g}米。"
            if preferences.max_transfer_count is not None:
                extra += f"换乘不超过{preferences.max_transfer_count}次。"
            if deadline:
                extra += (f"最晚{deadline.arrival_by.isoformat()}到达，"
                          f"预留{deadline.buffer_minutes}分钟。")
            prompt = (
                f"请重新查询规划原行程：{route}。原出发时间：{departure}。{extra}"
                "这次只生成新方案，不要保存，也不要放宽约束。"
                "原出发时间若已过期或时间含糊，请先向我确认新的时间；"
                "地点有歧义也须确认。"
            )
            return {"kind": kind, "snapshot": source, "replan_prompt": prompt}

    async def enable(self, saved_id: UUID, *, now=None):
        return await asyncio.to_thread(self._enable, saved_id, now or datetime.now(UTC))

    def _enable(self, saved_id, now):
        with self._db() as db:
            self._schema(db)
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM saved_multi_stop WHERE id=?", (str(saved_id),)
            ).fetchone()
            if not row:
                raise ValueError("请先保存完整行程，再开启守护")
            saved = SavedMultiStopJourney.model_validate_json(row[0])
            departure = saved.draft.result.request.departure_time
            if departure is None or departure.departure_at <= now:
                raise ValueError("开启守护需要未来的明确出发时间；请补充时间并重新规划保存")
            stamp = departure.departure_at.timestamp()
            db.execute(
                """INSERT INTO departure_watches
                (id,saved_id,snapshot,departure,due,status) VALUES (?,?,?,?,?,'pending')
                ON CONFLICT(saved_id) DO NOTHING""",
                (
                    str(uuid4()),
                    str(saved_id),
                    saved.model_dump_json(),
                    stamp,
                    max(now.timestamp(), stamp - 1800),
                ),
            )
        return next(row for row in self._list() if row["saved_id"] == str(saved_id))

    async def list(self):
        return await asyncio.to_thread(self._list)

    def _list(self):
        if not self.workspace.path.is_file():
            return []
        with self._db() as db:
            self._schema(db)
            rows = db.execute("""SELECT w.id,w.saved_id,w.status,w.attempts,w.checked_at,
                w.message,w.snapshot,w.departure,r.token FROM departure_watches w
                LEFT JOIN departure_watch_reads r ON w.id=r.id ORDER BY w.departure DESC"""
                              ).fetchall()
            items = []
            for row in rows:
                item = dict(
                    zip(
                        ("id", "saved_id", "status", "attempts", "checked_at", "message"),
                        row[:6],
                        strict=True,
                    )
                )
                payload = json.loads(row[6])
                if payload.get("kind") == "single":
                    saved = SingleWatchSnapshot.model_validate(payload).saved
                    title, kind = f"{saved.origin} → {saved.destination}", "single"
                else:
                    saved = SavedMultiStopJourney.model_validate(payload)
                    title = " → ".join(s.name for s in saved.draft.result.request.stops)
                    kind = "multi"
                token = self._token(row[4], row[5])
                item.update(
                    title=title,
                    kind=kind,
                    notification_token=token,
                    unread=bool(token and token != row[8]),
                    departure_at=datetime.fromtimestamp(row[7], UTC).isoformat(),
                )
                items.append(item)
            return items

    async def cancel(self, identity: UUID):
        await asyncio.to_thread(self._cancel, str(identity))

    def _cancel(self, identity):
        with self._db() as db:
            self._schema(db)
            db.execute("UPDATE departure_watches SET status='cancelled' WHERE id=?", (identity,))

    def _claim(self, now):
        if not self.workspace.path.is_file():
            return None
        with self._db() as db:
            self._schema(db)
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE departure_watches SET status='expired'
                WHERE departure<=? AND status IN ('pending','checking')""",
                (now.timestamp(),),
            )
            row = db.execute(
                """SELECT id,snapshot FROM departure_watches WHERE departure>?
                AND ((status='pending' AND due<=?) OR (status='checking' AND lease<=?))
                ORDER BY due LIMIT 1""",
                (now.timestamp(), now.timestamp(), now.timestamp()),
            ).fetchone()
            if row:
                db.execute(
                    """UPDATE departure_watches SET status='checking', attempts=attempts+1,
                    lease=? WHERE id=?""",
                    ((now + timedelta(minutes=3)).timestamp(), row[0]),
                )
            return row

    async def tick(self, *, now=None):
        now = now or datetime.now(UTC)
        row = await asyncio.to_thread(self._claim, now)
        if not row:
            return
        identity, payload = row
        try:
            async with asyncio.timeout(90):
                if json.loads(payload).get("kind") == "single":
                    if self.single_checker is None:
                        raise ValueError("单段守护未配置")
                    snapshot = SingleWatchSnapshot.model_validate_json(payload)
                    message = await self.single_checker.check(snapshot, now)
                    await asyncio.to_thread(self._finish, identity, "completed", message, now)
                    return
                saved = SavedMultiStopJourney.model_validate_json(payload)
                result = await self.planner.plan(saved.draft.result.request)
                original = next(
                    p
                    for p in saved.draft.result.selection.plans
                    if p.plan_id == saved.selected_plan_id
                )
                # Compare the user's selected modes, never silently substitute a recommendation.
                modes = [leg.leg.option.mode for leg in original.evaluation.legs]
                matches = [
                    p
                    for p in result.selection.plans
                    if p.evaluation.status == "feasible"
                    and [leg.leg.option.mode for leg in p.evaluation.legs] == modes
                ]
                notes = []
                if not matches:
                    raise ValueError("未获得与原选交通方式一致的完整可行方案")
                current = min(matches, key=lambda p: p.evaluation.totals.elapsed_s)
                delta = current.evaluation.totals.elapsed_s - original.evaluation.totals.elapsed_s
                if delta >= max(600, original.evaluation.totals.elapsed_s * 0.2):
                    notes.append(f"同方式新估计比保存方案增加约{round(delta / 60)}分钟")
                draft = MultiStopDraft(draft_id=saved.draft.draft_id, result=result, created_at=now)
                report = await self.weather.report(draft, current.plan_id, now=now)
                warnings = [f"{s.name}：{w}" for s in report.stops for w in s.warnings]
                notes.extend(warnings)
                expected = {s.stop_id: s.arrival_at for s in current.evaluation.stops}
                if (
                    report.plan_id != current.plan_id
                    or {s.stop_id: s.target_at for s in report.stops} != expected
                    or len(report.stops) != len(expected)
                    or any(s.status != "forecast" for s in report.stops)
                ):
                    raise ValueError("部分到达采样点天气未知")
                message = "；".join(notes) if notes else "本次采样未发现达到提示阈值的变化"
                message += (
                    "。这是同交通方式候选的新估计，不保证对应原公交班次。"
                    "路线来源：高德；天气来源：Open-Meteo（到达采样点）。"
                    "请自行决定是否重新规划；原保存行程未改变。"
                )
                await asyncio.to_thread(self._finish, identity, "completed", message, now)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.to_thread(
                self._finish,
                identity,
                "pending",
                "检查未完成：路线或天气数据不完整，稍后重试；不能判断行程正常。",
                now,
            )

    def _finish(self, identity, status, message, now):
        with self._db() as db:
            db.execute(
                """UPDATE departure_watches SET status=CASE
                WHEN ?='pending' AND attempts>=3 THEN 'failed' ELSE ? END,
                message=?,checked_at=?,due=? WHERE id=? AND status='checking'""",
                (
                    status,
                    status,
                    message,
                    now.isoformat(),
                    (now + timedelta(minutes=5)).timestamp(),
                    identity,
                ),
            )

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception:
                # Storage outages must not kill the persistent worker.
                logging.getLogger(__name__).exception("Departure watch worker failed")
            await asyncio.sleep(30)
