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
        assert sent == ["20260901-20261001", "202609", "20261001"]

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

        assert [call["dates"] for call in session.calls] == ["202603"] + [
            "202603%02d" % day for day in range(1, 32)
        ]
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
