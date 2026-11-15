"""Проверки отчёта: python3 -m unittest -v"""
import unittest
from datetime import date
from pathlib import Path

from report import build_report, parse_money, fmt_money

DATA = Path(__file__).parent / "data"
R = 100  # копеек в рубле


def run(d: str):
    reports, warnings = build_report(DATA, date.fromisoformat(d))
    by_c = {r.contract.id: r for r in reports}
    by_w = {l.work.id: l for r in reports for l in r.lines}
    return by_c, by_w, warnings


class MoneyTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_money("600000.00"), 60_000_000)
        self.assertEqual(parse_money("0.1"), 10)
        self.assertEqual(parse_money("15"), 1500)
        with self.assertRaises(ValueError):
            parse_money("1.005")

    def test_fmt(self):
        self.assertEqual(fmt_money(31_500_000), "315 000,00")
        self.assertEqual(fmt_money(5), "0,05")


class ReportDate20261110(unittest.TestCase):
    def setUp(self):
        self.c, self.w, self.warn = run("2026-11-10")

    def test_d1_totals(self):
        d1 = self.c["D-1"]
        self.assertEqual(d1.active_price, 500_000 * R)
        self.assertEqual(d1.paid, 315_000 * R)
        self.assertEqual(d1.advance, 15_000 * R)
        self.assertEqual(d1.remaining, 185_000 * R)
        self.assertEqual(d1.queued, 150_000 * R)
        self.assertEqual(d1.overpaid, 0)

    def test_d1_works(self):
        for wid in ("W-11", "W-12", "W-14"):
            self.assertEqual(self.w[wid].paid, 100_000 * R, wid)
            self.assertEqual(self.w[wid].remaining, 0, wid)
        self.assertEqual(self.w["W-14"].paid_linked, 15_000 * R)
        self.assertEqual(self.w["W-13"].status, "cancelled")
        self.assertEqual(self.w["W-13"].remaining, 0)
        self.assertEqual(self.w["W-15"].remaining, 100_000 * R)

    def test_d2(self):
        d2 = self.c["D-2"]
        self.assertEqual(d2.paid, 75_000 * R)          # P-8 ещё не оплачен
        self.assertEqual(d2.advance, 25_000 * R)
        self.assertEqual(d2.remaining, 15_000 * R)
        self.assertEqual(self.w["W-23"].status, "planned")  # принята только 11.11
        self.assertEqual(self.w["W-23"].remaining, 40_000 * R)

    def test_balance(self):
        """Сумма «выплачено» по работам + аванс + переплата = всего выплачено."""
        for r in self.c.values():
            self.assertEqual(sum(l.paid for l in r.lines) + r.advance + r.overpaid, r.paid)

    def test_no_data_warnings(self):
        self.assertEqual(self.warn, [])


class OtherDates(unittest.TestCase):
    def test_before_refund(self):
        c, _, _ = run("2026-09-05")
        self.assertEqual(c["D-1"].paid, 350_000 * R)   # P-2 ещё не вернули

    def test_after_report_date_overpayment(self):
        c, w, _ = run("2026-11-12")
        self.assertEqual(c["D-2"].paid, 95_000 * R)
        self.assertEqual(c["D-2"].overpaid, 5_000 * R)
        self.assertEqual(c["D-2"].remaining, 0)
        self.assertEqual(w["W-23"].remaining, 0)


if __name__ == "__main__":
    unittest.main()
