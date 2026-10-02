"""Shared test data."""
from datetime import date, timedelta

from custom_components.bwwb_water.parsers import Bill, LineItem


def make_bill(issue: date, present: float, cons: float, water: float = 43.97, sewer: float = 56.02) -> Bill:
    return Bill(
        invoice_date=issue,
        due_date=issue + timedelta(days=14),
        total_due=round(water + sewer, 2),
        period_start=issue - timedelta(days=28),
        period_end=issue,
        meter_number="000000001",
        previous_read=present - cons,
        previous_read_date=issue - timedelta(days=29),
        present_read=present,
        present_read_date=issue,
        consumption=cons,
        water_charges=water,
        sewer_charges=sewer,
        water_line_items=(LineItem("Base Service Charge", 28.49),),
        sewer_line_items=(LineItem("Base Service Charge", 26.80),),
    )


BILLS = {
    date(2026, 7, 2): make_bill(date(2026, 7, 2), 930, 5),
    date(2026, 8, 4): make_bill(date(2026, 8, 4), 935, 5),
    date(2026, 9, 2): make_bill(date(2026, 9, 2), 939, 4),
}
