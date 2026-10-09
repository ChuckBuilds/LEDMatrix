"""src.common.espn_dates: the ESPN site-API workarounds.

Two real upstream behaviours are pinned here, both observed on 2026-09-15 and
re-verified with desktop curl (see the module docstring):

* ``dates=YYYYMMDD-YYYYMMDD`` answers 400 for every sport, so a range has to be
  re-asked in months and days.
* ``limit`` over 500 truncates instead of erroring -- college-football returned
  25 of 68 games for a single Saturday at ``limit=1000``.

Nothing here touches the network. The fake session records what a caller would
have sent, which is the part that regressed.
"""

import threading
import time
from datetime import date, timedelta

import pytest

import src.common.espn_dates as espn_dates
from src.common.espn_dates import (
    ESPN_MAX_LIMIT,
    RANGE_RETRY_SECONDS,
    clamp_espn_limit,
    espn_date_chunks,
    fetch_espn_date_chunks,
    fetch_espn_scoreboard,
    merge_scoreboard_payloads,
    parse_espn_date_range,
)

URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


@pytest.fixture(autouse=True)
def forget_rejected_ranges(monkeypatch):
    """The rejected-range memo is process-wide; no test may inherit it."""
    monkeypatch.setattr(espn_dates, "_ranges_rejected_until", 0.0)


@pytest.fixture(autouse=True)
def nothing_is_settled_yet(monkeypatch):
    """Pin "today" before every date these tests use, so the settled-chunk
    memory stays out of tests that are not about it whatever the real date.
    TestSettledChunkCache moves it forward."""
    monkeypatch.setattr(espn_dates, "_utc_today", lambda: date(2000, 1, 1))


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"events": []}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(str(self.status_code) + " Client Error: Bad Request")


class FakeSession:
    """Answers 400 to day ranges, like ESPN does, and records every call."""

    def __init__(self, events_by_chunk=None, fail_chunks=()):
        self.calls = []
        self.events_by_chunk = events_by_chunk or {}
        self.fail_chunks = set(fail_chunks)

    def get(self, url, params=None, headers=None, timeout=None):
        params = params or {}
        dates = str(params.get("dates", ""))
        self.calls.append(params)
        if parse_espn_date_range(dates) is not None:
            return FakeResponse(400)
        if dates in self.fail_chunks:
            return FakeResponse(500)
        return FakeResponse(200, {"events": self.events_by_chunk.get(dates, [])})


def days_covered_by(chunks):
    """Expand chunks back into the days they stand for, in order."""
    covered = []
    for chunk in chunks:
        if len(chunk) == 6:
            day = date(int(chunk[:4]), int(chunk[4:]), 1)
            month = day.month
            while day.month == month:
                covered.append(day)
                day += timedelta(days=1)
        else:
            covered.append(date(int(chunk[:4]), int(chunk[4:6]), int(chunk[6:])))
    return covered


class TestClampLimit:
    """limit over 500 silently truncates upstream, so it must never be sent."""

    def test_the_limit_every_caller_used_is_pulled_back(self):
        assert clamp_espn_limit({"limit": 1000})["limit"] == ESPN_MAX_LIMIT

    def test_a_safe_limit_is_left_alone(self):
        assert clamp_espn_limit({"limit": 100})["limit"] == 100

    def test_the_boundary_value_is_kept(self):
        assert clamp_espn_limit({"limit": 500})["limit"] == 500

    @pytest.mark.parametrize("params", [{}, {"limit": None}, {"limit": "many"}])
    def test_absent_or_unparseable_limits_pass_through(self, params):
        assert clamp_espn_limit(params) == params

    def test_the_callers_dict_is_not_mutated(self):
        original = {"limit": 1000}
        clamp_espn_limit(original)
        assert original == {"limit": 1000}


class TestParseRange:
    def test_a_day_range_parses(self):
        assert parse_espn_date_range("20260801-20270301") == (
            date(2026, 8, 1),
            date(2027, 3, 1),
        )

    @pytest.mark.parametrize(
        "value",
        [
            "20260914",           # single day still works upstream
            "202609",             # month still works upstream
            "2026",               # season year still works upstream
            "20260801-",
            "not-a-date",
            "20270301-20260801",  # backwards
            "2026080-20270301",   # short half
            None,
            1234,
        ],
    )
    def test_everything_that_is_not_a_day_range_is_left_alone(self, value):
        assert parse_espn_date_range(value) is None


class TestChunks:
    """Chunks must tile the window exactly -- never reaching outside it."""

    def test_a_full_season_collapses_to_months_plus_one_day(self):
        assert espn_date_chunks(date(2026, 8, 1), date(2027, 3, 1)) == [
            "202608",
            "202609",
            "202610",
            "202611",
            "202612",
            "202701",
            "202702",
            "20270301",
        ]

    def test_a_two_day_window_stays_two_days(self):
        assert espn_date_chunks(date(2026, 9, 14), date(2026, 9, 15)) == [
            "20260914",
            "20260915",
        ]

    def test_an_exact_calendar_month_is_one_request(self):
        assert espn_date_chunks(date(2026, 9, 1), date(2026, 9, 30)) == ["202609"]

    def test_partial_edges_are_spelled_out_day_by_day(self):
        assert espn_date_chunks(date(2026, 8, 30), date(2026, 10, 2)) == [
            "20260830",
            "20260831",
            "202609",
            "20261001",
            "20261002",
        ]

    def test_a_single_day_window_is_one_day(self):
        assert espn_date_chunks(date(2026, 9, 14), date(2026, 9, 14)) == ["20260914"]

    def test_february_in_a_leap_year_is_still_one_month(self):
        assert espn_date_chunks(date(2028, 2, 1), date(2028, 2, 29)) == ["202802"]

    def test_the_29th_of_a_leap_february_is_not_swallowed(self):
        # A month chunk may only be used when it ends inside the window.
        assert espn_date_chunks(date(2028, 2, 1), date(2028, 2, 28)) == [
            "202802{:02d}".format(day) for day in range(1, 29)
        ]

    def test_a_year_boundary_is_crossed_cleanly(self):
        assert espn_date_chunks(date(2026, 12, 31), date(2027, 1, 31)) == [
            "20261231",
            "202701",
        ]

    @pytest.mark.parametrize(
        "start,end",
        [
            (date(2026, 8, 1), date(2027, 3, 1)),
            (date(2026, 8, 30), date(2026, 10, 2)),
            (date(2025, 9, 1), date(2026, 8, 1)),
            (date(2026, 9, 14), date(2026, 9, 15)),
        ],
    )
    def test_chunks_cover_every_day_exactly_once(self, start, end):
        expected = []
        day = start
        while day <= end:
            expected.append(day)
            day += timedelta(days=1)
        assert days_covered_by(espn_date_chunks(start, end)) == expected


class TestMerge:
    def test_events_are_deduplicated_by_id(self):
        merged = merge_scoreboard_payloads(
            [
                {"events": [{"id": "1"}, {"id": "2"}]},
                {"events": [{"id": "2"}, {"id": "3"}]},
            ]
        )
        assert [e["id"] for e in merged["events"]] == ["1", "2", "3"]

    def test_non_event_keys_come_from_the_first_payload_that_has_them(self):
        merged = merge_scoreboard_payloads(
            [
                {"events": [], "leagues": ["first"]},
                {"events": [], "leagues": ["second"], "season": 2026},
            ]
        )
        assert merged["leagues"] == ["first"]
        assert merged["season"] == 2026

    def test_an_empty_merge_still_has_an_events_list(self):
        assert merge_scoreboard_payloads([]) == {"events": []}


class TestFetch:
    def test_a_working_request_is_not_chunked(self):
        session = FakeSession({"20260913": [{"id": "1"}]})
        data = fetch_espn_scoreboard(session, URL, params={"dates": "20260913"})
        assert data["events"] == [{"id": "1"}]
        assert len(session.calls) == 1

    def test_limit_is_clamped_even_on_the_happy_path(self):
        session = FakeSession()
        fetch_espn_scoreboard(session, URL, params={"dates": "20260913", "limit": 1000})
        assert session.calls[0]["limit"] == ESPN_MAX_LIMIT

    def test_a_rejected_range_is_refetched_in_chunks(self):
        session = FakeSession(
            {"202609": [{"id": "a"}, {"id": "b"}], "20261001": [{"id": "c"}]}
        )
        data = fetch_espn_scoreboard(
            session, URL, params={"dates": "20260901-20261001", "limit": 1000}
        )
        assert [e["id"] for e in data["events"]] == ["a", "b", "c"]
        sent = [call["dates"] for call in session.calls]
        # Chunks race, so only the rejected range is pinned to a position --
        # the merged event order above is what has to stay deterministic.
        assert sent[0] == "20260901-20261001"
        assert sorted(sent[1:]) == ["202609", "20261001"]

    def test_chunk_requests_keep_the_clamped_limit(self):
        session = FakeSession({"202609": []})
        fetch_espn_scoreboard(
            session, URL, params={"dates": "20260901-20260930", "limit": 1000}
        )
        assert all(call["limit"] == ESPN_MAX_LIMIT for call in session.calls)

    def test_other_params_survive_chunking(self):
        session = FakeSession({"202609": []})
        fetch_espn_scoreboard(
            session, URL, params={"dates": "20260901-20260930", "groups": "80"}
        )
        assert session.calls[-1]["groups"] == "80"

    def test_one_bad_chunk_does_not_sink_the_season(self):
        session = FakeSession(
            {"202609": [{"id": "a"}], "20261001": [{"id": "c"}]},
            fail_chunks={"20261001"},
        )
        data = fetch_espn_scoreboard(session, URL, params={"dates": "20260901-20261001"})
        assert [e["id"] for e in data["events"]] == ["a"]

    def test_a_total_failure_raises_rather_than_looking_like_no_games(self):
        session = FakeSession({}, fail_chunks={"202609"})
        with pytest.raises(RuntimeError):
            fetch_espn_scoreboard(session, URL, params={"dates": "20260901-20260930"})

    def test_a_400_on_a_non_range_request_is_still_an_error(self):
        class AlwaysBad(FakeSession):
            def get(self, url, params=None, headers=None, timeout=None):
                self.calls.append(params or {})
                return FakeResponse(400)

        session = AlwaysBad()
        with pytest.raises(RuntimeError):
            fetch_espn_scoreboard(session, URL, params={"dates": "20260913"})
        assert len(session.calls) == 1


class TestMonthCap:
    """A month holding more than 500 events comes back cut at exactly 500.

    College baseball's March 2026 does this. Nothing in the response says more
    exist, so a full month chunk has to be re-asked day by day.
    """

    def test_a_full_month_is_re_asked_day_by_day(self):
        full = [{"id": "m%d" % i} for i in range(ESPN_MAX_LIMIT)]
        by_chunk = {"202603": full}
        by_chunk.update({"202603%02d" % day: [{"id": "d%d" % day}] for day in range(1, 32)})
        session = FakeSession(by_chunk)

        data = fetch_espn_date_chunks(session, URL, params={"dates": "20260301-20260331"})

        sent = [call["dates"] for call in session.calls]
        # The month has to be asked before its days can be known to be needed;
        # the days themselves race, so compare them as a set.
        assert sent[0] == "202603"
        assert sorted(sent[1:]) == ["202603%02d" % day for day in range(1, 32)]
        # The truncated month payload is dropped, not merged with the days.
        assert [event["id"] for event in data["events"]] == [
            "d%d" % day for day in range(1, 32)
        ]

    def test_a_month_under_the_cap_is_trusted(self):
        session = FakeSession({"202609": [{"id": "a"}] * 10})
        fetch_espn_date_chunks(session, URL, params={"dates": "20260901-20260930"})
        assert [call["dates"] for call in session.calls] == ["202609"]

    def test_chunks_ask_for_the_cap_even_when_the_caller_sent_no_limit(self):
        # ESPN's default page is 100 for NFL and 300 for college football --
        # smaller than a busy month.
        session = FakeSession({"202609": []})
        fetch_espn_date_chunks(session, URL, params={"dates": "20260901-20260930"})
        assert session.calls[0]["limit"] == ESPN_MAX_LIMIT

    def test_a_non_range_is_not_chunked(self):
        session = FakeSession()
        assert fetch_espn_date_chunks(session, URL, params={"dates": "202609"}) is None
        assert session.calls == []


class TestRejectedRangeMemo:
    """Live boards ask every 30s; a known-rejected range must not be re-sent."""

    def test_after_one_rejection_the_next_range_skips_straight_to_chunks(self):
        session = FakeSession({"20260914": [{"id": "a"}], "20260915": []})
        fetch_espn_scoreboard(session, URL, params={"dates": "20260914-20260915"})
        session.calls.clear()

        data = fetch_espn_scoreboard(session, URL, params={"dates": "20260914-20260915"})

        assert [call["dates"] for call in session.calls] == ["20260914", "20260915"]
        assert [event["id"] for event in data["events"]] == ["a"]

    def test_the_range_is_tried_again_once_the_memo_expires(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(espn_dates.time, "monotonic", lambda: clock[0])
        session = FakeSession()
        fetch_espn_scoreboard(session, URL, params={"dates": "20260914-20260915"})
        session.calls.clear()

        clock[0] += RANGE_RETRY_SECONDS + 1
        fetch_espn_scoreboard(session, URL, params={"dates": "20260914-20260915"})

        assert session.calls[0]["dates"] == "20260914-20260915"

    def test_a_400_on_a_single_day_does_not_mark_ranges_rejected(self):
        class AlwaysBad(FakeSession):
            def get(self, url, params=None, headers=None, timeout=None):
                self.calls.append(params or {})
                return FakeResponse(400)

        with pytest.raises(RuntimeError):
            fetch_espn_scoreboard(AlwaysBad(), URL, params={"dates": "20260913"})
        assert not espn_dates._ranges_known_rejected()

    def test_when_every_chunk_fails_the_range_itself_supplies_the_error(self):
        session = FakeSession(fail_chunks={"20260914", "20260915"})
        fetch_espn_scoreboard(session, URL, params={"dates": "20260801-20260801"})
        session.calls.clear()

        with pytest.raises(RuntimeError):
            fetch_espn_scoreboard(session, URL, params={"dates": "20260914-20260915"})
        assert session.calls[-1]["dates"] == "20260914-20260915"


class TestConcurrency:
    """Chunks go out in parallel, which must not change what comes back.

    A cold college-baseball season is ~130 chunks once February through May
    are re-asked day by day. Sequentially that outran the 20s plugin update()
    timeout on a Pi, so the requests now overlap -- but the merged payload has
    to stay exactly what the sequential version produced.
    """

    def test_events_keep_chunk_order_however_the_requests_race(self):
        # Answer the later chunks fastest, so completion order is the reverse
        # of chunk order and a naive gather would interleave them wrongly.
        class RacingSession(FakeSession):
            def get(self, url, params=None, headers=None, timeout=None):
                dates = str((params or {}).get("dates", ""))
                if len(dates) == 6:
                    time.sleep(0.02 / (int(dates[4:]) or 1))
                return super().get(url, params=params, headers=headers, timeout=timeout)

        session = RacingSession(
            {
                "202609": [{"id": "sep"}],
                "202610": [{"id": "oct"}],
                "202611": [{"id": "nov"}],
            }
        )
        data = fetch_espn_date_chunks(
            session, URL, params={"dates": "20260901-20261130"}
        )
        assert [event["id"] for event in data["events"]] == ["sep", "oct", "nov"]

    def test_a_capped_month_splices_its_days_in_place(self):
        # October is capped and expands to 31 days; September and November
        # must still bracket those days in the merged result.
        full = [{"id": "cap%d" % i} for i in range(ESPN_MAX_LIMIT)]
        by_chunk = {
            "202609": [{"id": "sep"}],
            "202610": full,
            "202611": [{"id": "nov"}],
        }
        by_chunk.update(
            {"202610%02d" % day: [{"id": "oct%02d" % day}] for day in range(1, 32)}
        )
        session = FakeSession(by_chunk)

        data = fetch_espn_date_chunks(
            session, URL, params={"dates": "20260901-20261130"}
        )

        expected = ["sep"] + ["oct%02d" % day for day in range(1, 32)] + ["nov"]
        assert [event["id"] for event in data["events"]] == expected

    def test_two_capped_months_expand_without_crossing_over(self):
        full = [{"id": "cap%d" % i} for i in range(ESPN_MAX_LIMIT)]
        by_chunk = {"202609": full, "202610": full}
        by_chunk.update(
            {"202609%02d" % day: [{"id": "s%02d" % day}] for day in range(1, 31)}
        )
        by_chunk.update(
            {"202610%02d" % day: [{"id": "o%02d" % day}] for day in range(1, 32)}
        )
        session = FakeSession(by_chunk)

        data = fetch_espn_date_chunks(
            session, URL, params={"dates": "20260901-20261031"}
        )

        expected = ["s%02d" % day for day in range(1, 31)] + [
            "o%02d" % day for day in range(1, 32)
        ]
        assert [event["id"] for event in data["events"]] == expected

    def test_a_failed_day_inside_a_capped_month_only_costs_that_day(self):
        full = [{"id": "cap%d" % i} for i in range(ESPN_MAX_LIMIT)]
        by_chunk = {"202610": full}
        by_chunk.update(
            {"202610%02d" % day: [{"id": "o%02d" % day}] for day in range(1, 32)}
        )
        session = FakeSession(by_chunk, fail_chunks={"20261015"})

        data = fetch_espn_date_chunks(
            session, URL, params={"dates": "20261001-20261031"}
        )

        expected = ["o%02d" % day for day in range(1, 32) if day != 15]
        assert [event["id"] for event in data["events"]] == expected

    def test_no_more_than_the_worker_cap_are_in_flight_at_once(self):
        live = {"now": 0, "peak": 0}
        guard = threading.Lock()

        class CountingSession(FakeSession):
            def get(self, url, params=None, headers=None, timeout=None):
                with guard:
                    live["now"] += 1
                    live["peak"] = max(live["peak"], live["now"])
                try:
                    time.sleep(0.01)
                    return super().get(
                        url, params=params, headers=headers, timeout=timeout
                    )
                finally:
                    with guard:
                        live["now"] -= 1

        session = CountingSession()
        fetch_espn_date_chunks(session, URL, params={"dates": "20260101-20261231"})

        assert live["peak"] <= espn_dates.ESPN_CHUNK_WORKERS
        assert live["peak"] > 1, "chunks should actually overlap"


class TestSettledChunkCache:
    """Days that ended three or more days ago are fetched once a day, not hourly.

    The scoreboards re-fetch a 22-day window every hour; on hdpi (2026-10-02)
    the 12 settled days were 68% of that window's bytes.
    """

    TODAY = date(2026, 10, 2)
    # The scoreboards' default window on that day: 14 back, 7 ahead.
    WINDOW = "20260918-20261009"

    @pytest.fixture(autouse=True)
    def frozen_today(self, monkeypatch):
        monkeypatch.setattr(espn_dates, "_utc_today", lambda: self.TODAY)
        # These count day requests; whole edge months are covered below.
        monkeypatch.setattr(espn_dates, "ESPN_MONTH_COVER_MIN_DAYS", 0)

    def _events(self):
        days = [(9, d) for d in range(18, 31)] + [(10, d) for d in range(1, 10)]
        return {"2026%02d%02d" % (m, d): [{"id": f"{m}-{d}"}] for m, d in days}

    def test_the_second_refresh_only_asks_for_unsettled_days(self):
        session = FakeSession(self._events())
        first = fetch_espn_scoreboard(session, URL, params={"dates": self.WINDOW})
        session.calls.clear()

        second = fetch_espn_scoreboard(session, URL, params={"dates": self.WINDOW})

        asked = sorted(call["dates"] for call in session.calls)
        # Sep 29 is the last settled day (today minus three).
        assert asked == ["20260930"] + ["202610%02d" % d for d in range(1, 10)]
        assert second["events"] == first["events"]  # same events, same order
        assert len(second["events"]) == 22

    def test_a_hit_is_a_fresh_copy(self):
        session = FakeSession(self._events())
        fetch_espn_date_chunks(session, URL, params={"dates": self.WINDOW})
        hit = fetch_espn_date_chunks(session, URL, params={"dates": self.WINDOW})
        hit["events"][0]["id"] = "mutated"
        again = fetch_espn_date_chunks(session, URL, params={"dates": self.WINDOW})
        assert again["events"][0]["id"] == "9-18"

    def test_other_params_are_part_of_the_key(self):
        session = FakeSession(self._events())
        fetch_espn_date_chunks(session, URL, params={"dates": self.WINDOW, "groups": 80})
        session.calls.clear()
        fetch_espn_date_chunks(session, URL, params={"dates": self.WINDOW})
        assert len(session.calls) == 22  # a different question, nothing reused

    def test_entries_expire_after_a_day(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(espn_dates.time, "monotonic", lambda: clock[0])
        session = FakeSession(self._events())
        fetch_espn_date_chunks(session, URL, params={"dates": self.WINDOW})
        clock[0] += espn_dates.SETTLED_CHUNK_TTL_SECONDS + 1
        session.calls.clear()
        fetch_espn_date_chunks(session, URL, params={"dates": self.WINDOW})
        assert len(session.calls) == 22

    def test_failed_chunks_are_not_remembered(self):
        session = FakeSession(self._events(), fail_chunks={"20260920"})
        fetch_espn_date_chunks(session, URL, params={"dates": self.WINDOW})
        session.fail_chunks.clear()
        session.calls.clear()
        data = fetch_espn_date_chunks(session, URL, params={"dates": self.WINDOW})
        assert "20260920" in [call["dates"] for call in session.calls]
        assert len(data["events"]) == 22

    def test_a_capped_month_is_not_remembered_but_its_days_are(self):
        full = [{"id": f"x{i}"} for i in range(ESPN_MAX_LIMIT)]
        session = FakeSession({"202608": full, "20260801": [{"id": "d1"}]})
        fetch_espn_date_chunks(session, URL, params={"dates": "20260801-20260831"})
        session.calls.clear()
        data = fetch_espn_date_chunks(session, URL, params={"dates": "20260801-20260831"})
        assert [call["dates"] for call in session.calls] == ["202608"]
        assert [event["id"] for event in data["events"]] == ["d1"]

    def test_memory_is_bounded(self, monkeypatch):
        monkeypatch.setattr(espn_dates, "SETTLED_CACHE_MAX_ENTRIES", 5)
        session = FakeSession(self._events())
        fetch_espn_date_chunks(session, URL, params={"dates": self.WINDOW})
        assert len(espn_dates._settled_chunks) == 5
        assert espn_dates._settled_bytes == sum(
            len(blob) for _, blob in espn_dates._settled_chunks.values())

    def test_single_day_requests_are_untouched(self):
        # The live path asks for today (or one day) as a plain request; that
        # never goes through chunks or the memory.
        session = FakeSession(self._events())
        for _ in range(2):
            fetch_espn_scoreboard(session, URL, params={"dates": "20260918"})
        assert len(session.calls) == 2


class TestEdgeMonths:
    """A window's partial edge months are asked whole and trimmed.

    The default scoreboard window -- a fortnight either side of today -- spans
    two partial months, so it used to cost 29 day requests per league. ESPN's
    ``dates=YYYYMMDD`` means a US Eastern day (verified against the live API
    on 2026-10-03, 417 of 417 soccer events), so a month answer trimmed to
    the window's Eastern days is what the day requests returned.
    """

    def test_a_fortnight_either_side_is_two_requests(self):
        planned = espn_dates.espn_request_chunks(date(2026, 9, 20), date(2026, 10, 18))
        assert planned == [
            ("202609", (date(2026, 9, 20), date(2026, 9, 30))),
            ("202610", (date(2026, 10, 1), date(2026, 10, 18))),
        ]

    def test_a_live_polls_two_days_stay_two_days(self):
        planned = espn_dates.espn_request_chunks(date(2026, 10, 2), date(2026, 10, 3))
        assert planned == [("20261002", None), ("20261003", None)]

    def test_the_threshold_is_inclusive(self):
        n = espn_dates.ESPN_MONTH_COVER_MIN_DAYS
        short = espn_dates.espn_request_chunks(date(2026, 10, 1), date(2026, 10, n - 1))
        assert [chunk for chunk, _ in short] == [
            "202610%02d" % day for day in range(1, n)]
        enough = espn_dates.espn_request_chunks(date(2026, 10, 1), date(2026, 10, n))
        assert enough == [("202610", (date(2026, 10, 1), date(2026, 10, n)))]

    def test_whole_months_and_short_edges_are_unchanged(self):
        planned = espn_dates.espn_request_chunks(date(2026, 8, 30), date(2026, 10, 2))
        assert planned == [
            ("20260830", None), ("20260831", None), ("202609", None),
            ("20261001", None), ("20261002", None),
        ]

    def test_without_time_zone_data_edges_stay_days(self, monkeypatch):
        monkeypatch.setattr(espn_dates, "_EASTERN", None)
        planned = espn_dates.espn_request_chunks(date(2026, 9, 20), date(2026, 10, 18))
        assert len(planned) == 29
        assert all(trim is None for _, trim in planned)

    @pytest.mark.parametrize("start,end", [
        (date(2026, 9, 20), date(2026, 10, 18)),
        (date(2026, 1, 25), date(2026, 3, 3)),
        (date(2026, 12, 20), date(2027, 1, 9)),
        (date(2026, 10, 5), date(2026, 10, 9)),
    ])
    def test_the_planned_requests_still_cover_every_day_exactly_once(self, start, end):
        covered = []
        for chunk, trim in espn_dates.espn_request_chunks(start, end):
            if trim is None:
                covered.extend(days_covered_by([chunk]))
            else:
                assert chunk == trim[0].strftime("%Y%m") == trim[1].strftime("%Y%m")
                covered.extend(trim[0] + timedelta(days=offset)
                               for offset in range((trim[1] - trim[0]).days + 1))
        expected = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
        assert covered == expected

    def test_a_trimmed_month_keeps_only_the_windows_eastern_days(self):
        september = [
            # 03:30Z on the 20th is still the 19th in New York: outside.
            {"id": "before", "date": "2026-09-20T03:30Z"},
            {"id": "first", "date": "2026-09-20T14:00Z"},
            {"id": "late", "date": "2026-09-30T23:30Z"},
        ]
        october = [
            {"id": "oct1", "date": "2026-10-01T19:00Z"},
            # 03:30Z on the 19th is the evening of the 18th in New York: inside.
            {"id": "last", "date": "2026-10-19T03:30Z"},
            {"id": "after", "date": "2026-10-19T14:00Z"},
            {"id": "undated"},
        ]
        session = FakeSession({"202609": september, "202610": october})

        data = fetch_espn_date_chunks(session, URL, params={"dates": "20260920-20261018"})

        assert sorted(call["dates"] for call in session.calls) == ["202609", "202610"]
        # An event with no readable date is kept, never dropped on a guess.
        assert [e["id"] for e in data["events"]] == ["first", "late", "oct1", "last", "undated"]

    def test_a_remembered_month_is_trimmed_per_window(self, monkeypatch):
        """The settled-chunk memory holds the month whole; each window trims it."""
        monkeypatch.setattr(espn_dates, "_utc_today", lambda: date(2026, 12, 20))
        espn_dates.clear_settled_chunk_cache()
        november = [
            {"id": "early", "date": "2026-11-02T18:00Z"},
            {"id": "late", "date": "2026-11-25T18:00Z"},
        ]
        session = FakeSession({"202611": november})

        late = fetch_espn_date_chunks(session, URL, params={"dates": "20261120-20261215"})
        session.calls.clear()
        early = fetch_espn_date_chunks(session, URL, params={"dates": "20261101-20261110"})

        assert [e["id"] for e in late["events"]] == ["late"]
        assert [e["id"] for e in early["events"]] == ["early"]
        assert not any(call["dates"] == "202611" for call in session.calls)
        espn_dates.clear_settled_chunk_cache()

    def test_eastern_standard_time_is_honoured_after_the_clocks_change(self):
        # 2026-11-01 ends daylight saving: Eastern is UTC-5 from then on.
        november = [
            {"id": "out", "date": "2026-11-15T04:30Z"},  # Nov 14, 23:30 EST
            {"id": "in", "date": "2026-11-15T05:30Z"},   # Nov 15, 00:30 EST
        ]
        session = FakeSession({"202611": november})
        data = fetch_espn_date_chunks(session, URL, params={"dates": "20261115-20261121"})
        assert [e["id"] for e in data["events"]] == ["in"]

    def test_a_capped_edge_month_re_asks_only_the_windows_days(self):
        full = [{"id": "cap%d" % i, "date": "2026-10-05T18:00Z"} for i in range(ESPN_MAX_LIMIT)]
        by_chunk = {"202610": full}
        by_chunk.update({"202610%02d" % day: [{"id": "o%02d" % day}] for day in range(1, 32)})
        session = FakeSession(by_chunk)

        data = fetch_espn_date_chunks(session, URL, params={"dates": "20261001-20261010"})

        sent = [call["dates"] for call in session.calls]
        assert sent[0] == "202610"
        assert sorted(sent[1:]) == ["202610%02d" % day for day in range(1, 11)]
        assert [e["id"] for e in data["events"]] == ["o%02d" % day for day in range(1, 11)]


class TestProcessWideChunkCap:
    """The chunk cap holds across windows, not per window.

    A soccer board starting eight leagues fetches sixteen windows at once.
    With a pool of ``ESPN_CHUNK_WORKERS`` each, ~40 requests were in flight
    and every one past a session's pool opened a connection -- and a DNS
    lookup. On ledpi that was ~90 NameResolutionErrors per start.
    """

    def test_concurrent_windows_share_one_budget(self):
        live = {"now": 0, "peak": 0}
        guard = threading.Lock()

        class CountingSession(FakeSession):
            def get(self, url, params=None, headers=None, timeout=None):
                with guard:
                    live["now"] += 1
                    live["peak"] = max(live["peak"], live["now"])
                try:
                    time.sleep(0.01)
                    return super().get(url, params=params, headers=headers, timeout=timeout)
                finally:
                    with guard:
                        live["now"] -= 1

        sessions = [CountingSession() for _ in range(6)]
        # Six leagues, so the fetch service cannot merge them into one, on a
        # host with no token bucket: earlier tests may have spent ESPN's
        # burst, and a bucket paced at 20/s would serialise these by itself.
        threads = [
            threading.Thread(target=fetch_espn_date_chunks,
                             args=(session, "https://scores.example.test/league%d" % index),
                             kwargs={"params": {"dates": "20260101-20261231"}})
            for index, session in enumerate(sessions)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert all(len(session.calls) == 12 for session in sessions)
        assert live["peak"] <= espn_dates.ESPN_CHUNK_WORKERS
        assert live["peak"] > 1, "chunks should still overlap"


def test_a_fresh_process_skips_the_doomed_range_request():
    """Every start used to spend one 400 per window learning that ranges are
    still rejected -- eleven at once from a soccer board. A new process now
    starts inside the retry period instead."""
    import subprocess
    import sys
    from pathlib import Path

    out = subprocess.run(
        [sys.executable, "-c",
         "import src.common.espn_dates as e; print(e._ranges_known_rejected())"],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True, text=True, timeout=60,
    )
    assert out.stdout.strip() == "True", out.stderr
