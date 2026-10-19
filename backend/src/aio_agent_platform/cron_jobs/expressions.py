"""New input uses standard cron weekdays; persisted legacy numbers retain APScheduler semantics."""

from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TIMEZONE = ZoneInfo("Asia/Shanghai")


def normalize_cron(expr: str, *, legacy: bool = False) -> str:
    parts = expr.lower().split()
    if len(parts) != 5:
        raise ValueError("Cron 必须包含 5 个字段：分 时 日 月 周")
    dow = parts[4]
    if dow != "*":
        # Canonical names keep scheduler behavior independent of numeric conventions.
        days: set[int] = set()
        names = {name: (i if legacy else (i + 1) % 7) for i, name in enumerate(WEEKDAYS)}

        def number(value: str) -> int:
            n = names[value] if value in names else int(value)
            if not 0 <= n <= (6 if legacy else 7):
                raise ValueError("星期数字必须为 0–7（0/7=周日，1=周一）")
            return n

        for item in dow.split(","):
            segment, *step_parts = item.split("/")
            if len(step_parts) > 1:
                raise ValueError("非法星期步长")
            step = int(step_parts[0]) if step_parts else 1
            if step < 1:
                raise ValueError("星期步长必须大于零")
            if segment == "*":
                start, end = 0, 6
            elif "-" in segment:
                start_text, end_text = segment.split("-")
                start, end = number(start_text), number(end_text)
                # Named mon-sun is ordered Monday through Sunday.
                if not legacy and end_text == "sun" and start != 0:
                    end = 7
            else:
                start = number(segment)
                end = 6 if step_parts else start
            if start > end:
                raise ValueError("星期范围起点不能大于终点")
            days.update(n if legacy else (n - 1) % 7 for n in range(start, end + 1, step))
        parts[4] = ",".join(WEEKDAYS[n] for n in sorted(days))
    normalized = " ".join(parts)
    CronTrigger.from_crontab(normalized, timezone=TIMEZONE)
    return normalized
