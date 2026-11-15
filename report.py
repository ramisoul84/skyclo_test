#!/usr/bin/env python3
"""Отчёт по оплате работ и договоров на дату.

Запуск:
    python3 report.py --date 2026-11-10
    python3 report.py --date 2026-11-10 --data data --csv report.csv

Только стандартная библиотека Python 3.9+. Деньги считаются в копейках (int), без float.
Все допущения описаны в DECISIONS.md.
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------- деньги

def parse_money(text: str) -> int:
    """'600000.00' -> 60000000 копеек. Без float."""
    s = text.strip().replace(" ", "").replace(",", ".")
    if not s:
        raise ValueError("пустая сумма")
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    rub, _, kop = s.partition(".")
    if not rub.isdigit() or (kop and not kop.isdigit()) or len(kop) > 2:
        raise ValueError(f"некорректная сумма: {text!r}")
    value = int(rub) * 100 + int(kop.ljust(2, "0") or 0)
    return -value if neg else value


def fmt_money(kop: int) -> str:
    sign = "-" if kop < 0 else ""
    rub, k = divmod(abs(kop), 100)
    return f"{sign}{rub:,}".replace(",", " ") + f",{k:02d}"


def parse_date(text: str) -> Optional[date]:
    s = text.strip()
    return date.fromisoformat(s) if s else None


# ---------------------------------------------------------------- данные

@dataclass
class Contract:
    id: str
    contractor: str
    amount: int
    signed_at: Optional[date]


@dataclass
class Work:
    id: str
    contract_id: str
    title: str
    price: int
    status: str
    completed_at: Optional[date]
    cancelled_at: Optional[date]

    def status_on(self, d: date) -> str:
        """Статус на дату восстанавливаем по датам, а не по колонке status (решение 22)."""
        if self.cancelled_at and self.cancelled_at <= d:
            return "cancelled"
        if self.completed_at and self.completed_at <= d:
            return "done"
        return "planned"


@dataclass
class Payment:
    id: str
    contract_id: str
    work_id: str
    purpose: str
    amount: int
    status: str
    created_at: Optional[date]
    paid_at: Optional[date]
    refunded_at: Optional[date]

    def net_on(self, d: date) -> int:
        """Сколько денег по платежу реально у подрядчика на дату (решения 4, 6, 9)."""
        if not self.paid_at or self.paid_at > d:
            return 0
        if self.refunded_at and self.refunded_at <= d:
            return 0
        return self.amount

    def queued_on(self, d: date) -> bool:
        """Заведён в учёт к дате, но деньги ещё не ушли (решение 7)."""
        return bool(self.created_at and self.created_at <= d
                    and (not self.paid_at or self.paid_at > d))

    def refunded_on(self, d: date) -> bool:
        return bool(self.paid_at and self.paid_at <= d
                    and self.refunded_at and self.refunded_at <= d)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items()}
                for row in csv.DictReader(f)]


def load(data_dir: Path) -> tuple[list[Contract], list[Work], list[Payment]]:
    contracts = [Contract(r["contract_id"], r["contractor"], parse_money(r["amount"]),
                          parse_date(r["signed_at"]))
                 for r in read_csv(data_dir / "contracts.csv")]
    works = [Work(r["work_id"], r["contract_id"], r["title"], parse_money(r["price"]),
                  r["status"], parse_date(r["completed_at"]), parse_date(r["cancelled_at"]))
             for r in read_csv(data_dir / "works.csv")]
    payments = [Payment(r["payment_id"], r["contract_id"], r["work_id"], r["purpose"],
                        parse_money(r["amount"]), r["status"], parse_date(r["created_at"]),
                        parse_date(r["paid_at"]), parse_date(r["refunded_at"]))
                for r in read_csv(data_dir / "payments.csv")]
    return contracts, works, payments


# ---------------------------------------------------------------- проверки данных

def validate(contracts: list[Contract], works: list[Work], payments: list[Payment]) -> list[str]:
    warn: list[str] = []
    cids = {c.id for c in contracts}
    wmap = {w.id: w for w in works}
    for w in works:
        if w.contract_id not in cids:
            warn.append(f"Работа {w.id}: договор {w.contract_id} не найден.")
        if w.status not in ("planned", "done", "cancelled"):
            warn.append(f"Работа {w.id}: неизвестный статус {w.status!r}.")
        if w.status == "done" and not w.completed_at:
            warn.append(f"Работа {w.id}: статус done без даты приёмки.")
        if w.status == "cancelled" and not w.cancelled_at:
            warn.append(f"Работа {w.id}: статус cancelled без даты отмены.")
    for p in payments:
        if p.contract_id not in cids:
            warn.append(f"Платёж {p.id}: договор {p.contract_id} не найден.")
        if p.work_id:
            w = wmap.get(p.work_id)
            if not w:
                warn.append(f"Платёж {p.id}: работа {p.work_id} не найдена.")
            elif w.contract_id != p.contract_id:
                warn.append(f"Платёж {p.id}: работа {p.work_id} относится к договору "
                            f"{w.contract_id}, а платёж - к {p.contract_id}.")
        if p.status not in ("paid", "queued", "refunded"):
            warn.append(f"Платёж {p.id}: неизвестный статус {p.status!r}.")
        if p.status in ("paid", "refunded") and not p.paid_at:
            warn.append(f"Платёж {p.id}: статус {p.status} без даты оплаты.")
        if p.status == "refunded" and not p.refunded_at:
            warn.append(f"Платёж {p.id}: статус refunded без даты возврата.")
        if p.status == "queued" and p.paid_at:
            warn.append(f"Платёж {p.id}: статус queued, но есть дата оплаты.")
        if p.amount <= 0:
            warn.append(f"Платёж {p.id}: неположительная сумма.")
    for c in contracts:
        total = sum(w.price for w in works if w.contract_id == c.id)
        if total != c.amount:
            warn.append(f"Договор {c.id}: сумма договора {fmt_money(c.amount)} не равна "
                        f"сумме цен работ {fmt_money(total)} (решение 21).")
    return warn


# ---------------------------------------------------------------- расчёт

@dataclass
class WorkLine:
    work: Work
    status: str          # статус на дату отчёта
    paid_linked: int     # из платежей с привязкой к работе
    paid_allocated: int  # распределено из платежей без привязки
    remaining: int

    @property
    def paid(self) -> int:
        return self.paid_linked + self.paid_allocated


@dataclass
class ContractReport:
    contract: Contract
    lines: list[WorkLine]
    active_price: int    # цена неотменённых работ
    paid: int            # всего у подрядчика на дату
    advance: int         # оплачено вперёд за непринятые работы (не разнесено)
    overpaid: int        # оплачено сверх цены всех неотменённых работ
    remaining: int       # осталось оплатить по договору
    queued: int
    notes: list[str] = field(default_factory=list)


def build_contract(c: Contract, works: list[Work], payments: list[Payment], d: date) -> ContractReport:
    cworks = [w for w in works if w.contract_id == c.id]
    cpays = [p for p in payments if p.contract_id == c.id]
    status = {w.id: w.status_on(d) for w in cworks}
    notes: list[str] = []

    # 1. Платежи с привязкой - на свою работу, не больше её цены (решения 3, 13, 14).
    linked = {w.id: 0 for w in cworks}
    pool = 0
    for p in cpays:
        net = p.net_on(d)
        if not net:
            continue
        if p.work_id in linked and status[p.work_id] != "cancelled":
            linked[p.work_id] += net
        else:
            if p.work_id:
                notes.append(f"Платёж {p.id} привязан к отменённой/неизвестной работе "
                             f"{p.work_id} - учтён как платёж по договору.")
            pool += net
    for w in cworks:
        excess = linked[w.id] - w.price
        if excess > 0:
            linked[w.id] = w.price
            pool += excess
            notes.append(f"По работе {w.id} привязано больше цены - излишек "
                         f"{fmt_money(excess)} учтён как платёж по договору.")

    # 2. Платежи без привязки - на принятые работы по дате приёмки (решения 1, 2, 17).
    allocated = {w.id: 0 for w in cworks}
    done = sorted((w for w in cworks if status[w.id] == "done"),
                  key=lambda w: (w.completed_at, w.id))
    for w in done:
        take = min(pool, w.price - linked[w.id])
        allocated[w.id] = take
        pool -= take

    lines = []
    for w in sorted(cworks, key=lambda w: w.id):
        st = status[w.id]
        paid = linked[w.id] + allocated[w.id]
        rem = 0 if st == "cancelled" else w.price - paid   # решение 15
        lines.append(WorkLine(w, st, linked[w.id], allocated[w.id], rem))

    active = sum(w.price for w in cworks if status[w.id] != "cancelled")   # решение 16
    total_paid = sum(p.net_on(d) for p in cpays)
    open_planned = sum(l.remaining for l in lines if l.status == "planned")
    advance = min(pool, open_planned)
    overpaid = pool - advance                                               # решение 18
    remaining = max(active - total_paid, 0)
    queued = sum(p.amount for p in cpays if p.queued_on(d))

    for p in cpays:
        if p.refunded_on(d):
            notes.append(f"Платёж {p.id} ({p.purpose}, {fmt_money(p.amount)}) возвращён "
                         f"{p.refunded_at} - не учтён.")
        if p.queued_on(d):
            notes.append(f"Платёж {p.id} ({p.purpose}, {fmt_money(p.amount)}) в очереди "
                         f"на оплату - в «выплачено» не входит.")
    if active != c.amount:
        notes.append(f"Сумма договора по документу {fmt_money(c.amount)}, без отменённых "
                     f"работ - {fmt_money(active)}. «Осталось» считается от {fmt_money(active)}.")
    if overpaid:
        notes.append(f"ПЕРЕПЛАТА {fmt_money(overpaid)}: выплачено больше цены всех "
                     f"неотменённых работ.")
    return ContractReport(c, lines, active, total_paid, advance, overpaid, remaining, queued, notes)


def build_report(data_dir: Path, d: date) -> tuple[list[ContractReport], list[str]]:
    contracts, works, payments = load(data_dir)
    warnings = validate(contracts, works, payments)
    return [build_contract(c, works, payments, d) for c in contracts], warnings


# ---------------------------------------------------------------- вывод

STATUS_RU = {"done": "принята", "planned": "не принята", "cancelled": "отменена"}


def render_text(reports: list[ContractReport], warnings: list[str], d: date) -> str:
    out = [f"ОТЧЁТ ОБ ОПЛАТЕ РАБОТ НА {d.strftime('%d.%m.%Y')}", ""]
    hdr = f"{'Работа':<6} {'Название':<28} {'Статус':<11} {'Цена':>14} {'Выплачено':>14} {'Осталось':>14}"
    for r in reports:
        c = r.contract
        out.append(f"Договор {c.id} - {c.contractor}, сумма {fmt_money(c.amount)}")
        out.append(hdr)
        out.append("-" * len(hdr))
        for l in r.lines:
            out.append(f"{l.work.id:<6} {l.work.title[:28]:<28} {STATUS_RU[l.status]:<11} "
                       f"{fmt_money(l.work.price):>14} {fmt_money(l.paid):>14} "
                       f"{fmt_money(l.remaining):>14}")
        out.append("-" * len(hdr))
        out.append(f"  Цена неотменённых работ:          {fmt_money(r.active_price):>14}")
        out.append(f"  Выплачено подрядчику всего:       {fmt_money(r.paid):>14}")
        out.append(f"    в т.ч. аванс за непринятые:     {fmt_money(r.advance):>14}")
        if r.overpaid:
            out.append(f"    в т.ч. переплата:               {fmt_money(r.overpaid):>14}")
        by_works = sum(l.remaining for l in r.lines)
        out.append(f"  Осталось по работам (сумма колонки): {fmt_money(by_works):>11}")
        out.append(f"  Осталось оплатить по договору:    {fmt_money(r.remaining):>14}"
                   + (f"  (= {fmt_money(by_works)} - аванс {fmt_money(r.advance)})"
                      if r.advance else ""))
        out.append(f"  В очереди на оплату:              {fmt_money(r.queued):>14}")
        for n in r.notes:
            out.append(f"  * {n}")
        out.append("")
    if warnings:
        out.append("Предупреждения по данным:")
        out += [f"  ! {w}" for w in warnings]
    return "\n".join(out).rstrip() + "\n"


def write_csv(reports: list[ContractReport], path: Path) -> None:
    def m(k: int) -> str:
        sign = "-" if k < 0 else ""
        return f"{sign}{abs(k) // 100}.{abs(k) % 100:02d}"

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["contract_id", "work_id", "title", "status_on_date", "price", "paid",
                    "remaining", "advance", "overpaid", "queued"])
        for r in reports:
            for l in r.lines:
                w.writerow([r.contract.id, l.work.id, l.work.title, l.status,
                            m(l.work.price), m(l.paid), m(l.remaining), "", "", ""])
            w.writerow([r.contract.id, "ИТОГО", r.contract.contractor, "",
                        m(r.active_price), m(r.paid), m(r.remaining),
                        m(r.advance), m(r.overpaid), m(r.queued)])


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Отчёт по оплате работ на дату.")
    ap.add_argument("--date", required=True, help="дата отчёта, ГГГГ-ММ-ДД (включительно)")
    ap.add_argument("--data", default=str(Path(__file__).parent / "data"),
                    help="папка с CSV (по умолчанию ./data)")
    ap.add_argument("--csv", help="дополнительно сохранить отчёт в CSV (разделитель ;)")
    args = ap.parse_args(argv)
    try:
        d = date.fromisoformat(args.date)
    except ValueError:
        ap.error("дата должна быть в формате ГГГГ-ММ-ДД")
    reports, warnings = build_report(Path(args.data), d)
    sys.stdout.write(render_text(reports, warnings, d))
    if args.csv:
        write_csv(reports, Path(args.csv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
