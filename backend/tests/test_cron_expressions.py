from datetime import datetime

import pytest
from apscheduler.triggers.cron import CronTrigger

from aio_agent_platform.cron_jobs.expressions import TIMEZONE, normalize_cron


@pytest.mark.parametrize(('input_day', 'expected'), [
    ('0', 'sun'), ('7', 'sun'), ('1-5', 'mon,tue,wed,thu,fri'),
    ('mon-fri', 'mon,tue,wed,thu,fri'), ('mon-sun', 'mon,tue,wed,thu,fri,sat,sun'),
    ('*/2', 'tue,thu,sat,sun'), ('0,1,7', 'mon,sun'),
])
def test_new_input_uses_standard_weekdays(input_day, expected):
    assert normalize_cron(f'0 18 * * {input_day}') == f'0 18 * * {expected}'


def test_actual_trigger_matches_sunday_and_weekdays():
    now = datetime(2026, 10, 19, tzinfo=TIMEZONE)  # Monday
    sunday = CronTrigger.from_crontab(normalize_cron('0 18 * * 0'), timezone=TIMEZONE)
    assert sunday.get_next_fire_time(None, now).isoweekday() == 7
    weekday = CronTrigger.from_crontab(normalize_cron('30 14 * * 1-5'), timezone=TIMEZONE)
    assert weekday.get_next_fire_time(None, now).isoweekday() == 1
    assert normalize_cron('0 18 * * 0', legacy=True) == '0 18 * * mon'


@pytest.mark.parametrize('expr', ['* * *', '0 18 * * 8', '0 18 * * 5-1', '0 18 * * */0', '60 25 * * sun'])
def test_invalid_cron_rejected(expr):
    with pytest.raises(ValueError):
        normalize_cron(expr)
