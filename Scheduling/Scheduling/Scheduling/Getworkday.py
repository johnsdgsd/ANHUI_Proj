import chinese_calendar as calendar
from datetime import datetime, timedelta

def Getworkday(year_month):
    year = int(year_month)// 100
    month = int(year_month) % 100

    first_day = datetime(year, month, 1)
    if month == 12:
        last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = datetime(year, month + 1, 1) - timedelta(days=1)

    workdays = 0
    current_day = first_day
    while current_day <= last_day:
        if calendar.is_workday(current_day):
            workdays += 1
        current_day += timedelta(days=1)

    return workdays