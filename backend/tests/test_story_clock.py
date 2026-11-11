import pytest

from app.story_clock import StoryTime, parse_clock, parse_code, parse_stored_time

NOW = parse_clock("WED 14:00")


def test_clock_is_story_time_not_real_time():
    assert NOW == StoryTime(2, 14 * 60)


def test_weekdays_resolve_against_story_clock():
    assert parse_code("FRI 18:00", NOW) == StoryTime(4, 18 * 60)
    assert parse_code("SUN", NOW) == StoryTime(6, None)
    assert parse_code("MON", NOW).day == 7            # Monday has passed -> next Monday
    assert parse_code("WED", NOW).day == 9            # "Wednesday" said on Wednesday -> next week
    assert parse_code("NEXT WED", NOW).day == 9
    assert parse_code("NEXT FRI", NOW).day == 11
    assert parse_code("TODAY 17:00", NOW) == StoryTime(2, 17 * 60)
    assert parse_code("TOMORROW", NOW).day == 3


def test_bad_codes_are_rejected():
    assert parse_code("sunday evening", NOW) is None
    assert parse_code("SUN 25:00", NOW) is None
    assert parse_code("", NOW) is None


def test_spoken_restatement_uses_clock_times():
    assert StoryTime(6, 18 * 60).spoken() == "6 pm on Sunday"
    assert StoryTime(5, 12 * 60).spoken() == "noon on Saturday"
    assert StoryTime(4, 9 * 60 + 30).spoken() == "9:30 am on Friday"
    assert StoryTime(9, None).spoken() == "next Wednesday"


@pytest.mark.parametrize("code", ["TODAY 18:00", "TODAY", "TOMORROW", "FRI 18:00", "NEXT WED 09:30"])
def test_resolved_time_round_trip_does_not_move_week(code):
    value = parse_code(code, NOW)
    assert parse_stored_time(value.code(), NOW, value.storage()) == value
    assert parse_stored_time(value.code(), NOW) == value  # legacy save without metadata


@pytest.mark.parametrize("metadata", [
    {"day": 2, "minute": 1440}, {"day": 2}, {"day": True, "minute": 1080},
    {"day": 9, "minute": 1080}, "invalid",
])
def test_invalid_or_inconsistent_saved_time_fails_closed(metadata):
    assert parse_stored_time("WED 18:00", NOW, metadata) is None
