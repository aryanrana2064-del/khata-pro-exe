"""Localhost bridge between the phone remote (Node process) and the khata service.

The Node server runs in its own process; this tiny HTTP API lets it read customers/summary/pending
and add entries through KhataService — so every phone entry gets the same validation, duplicate
checks, credit limits, audit log and 24-hour Daily Entry hold as the desktop UI.

All database calls run on the Qt main thread: HTTP handlers enqueue a callable and wait, and a
QTimer in MainWindow drains the queue through ``Bridge.process``.
"""
from __future__ import annotations

import hmac
import json
import os
import queue
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
from datetime import date as _date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import exports
from .service import (
    CreditLimitError,
    DuplicateEntryError,
    KhataError,
    NotFoundError,
    ValidationError,
)

BRIDGE_PORT = int(os.environ.get("VX7_BRIDGE_PORT", "8767"))
_MAX_BODY = 64 * 1024


def _money_paise(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _customer_json(summary) -> dict:
    c = summary.customer
    return {
        "id": c.id,
        "name": c.name,
        "mobile": c.mobile or "",
        "address": c.address or "",
        "credit_limit_paise": c.credit_limit_paise or 0,
        "udhaar_paise": summary.total_udhaar,
        "jama_paise": summary.total_jama,
        "balance_paise": summary.balance,
        "txn_count": summary.txn_count,
        "last_txn_date": summary.last_txn_date,
    }


def _pending_json(p) -> dict:
    return {
        "id": p.id,
        "customer_id": p.customer_id,
        "customer_name": p.customer_name,
        "txn_type": p.txn_type,
        "txn_date": p.txn_date,
        "item": p.item,
        "quantity": p.quantity,
        "rate_paise": p.rate_paise,
        "amount_paise": p.amount_paise,
        "notes": p.notes or "",
        "due_date": p.due_date,
        "created_at": p.created_at,
        "post_at": p.post_at,
    }


def _txn_json(t) -> dict:
    return {
        "id": t.id,
        "customer_id": t.customer_id,
        "txn_type": t.txn_type,
        "txn_date": t.txn_date,
        "item": t.item,
        "quantity": t.quantity,
        "rate_paise": t.rate_paise,
        "amount_paise": t.amount_paise,
        "notes": t.notes or "",
        "created_at": t.created_at,
        "due_date": t.due_date,
    }


def _type_from_text(value) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return "UDHAAR"
    if text.startswith("j") or text in ("jama", "credit", "diya", "aaya", "received", "cr"):
        return "JAMA"
    if text.startswith("u") or text in ("udhaar", "debit", "liya", "gaya", "given", "dr"):
        return "UDHAAR"
    return "UDHAAR"


class Bridge:
    def __init__(self, service, port: int = BRIDGE_PORT):
        self.svc = service
        self.port = port
        token = service.get_setting("phone_bridge_token", "")
        if not token:
            token = secrets.token_urlsafe(24)
            service.set_setting("phone_bridge_token", token)
        self.token = token
        self._queue: queue.Queue = queue.Queue()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ lifecycle
    def start(self) -> bool:
        if self._server is not None:
            return True
        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", self.port), self._handler_class())
        except OSError:
            self._server = None
            return False
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()

    def process(self) -> None:
        """Run queued bridge work on the Qt main thread (called from a QTimer)."""
        for _ in range(20):
            try:
                fn, box, done = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                box["result"] = fn(self.svc)
            except Exception as exc:  # noqa: BLE001 - forwarded to the HTTP thread
                box["error"] = exc
            finally:
                done.set()

    def _run_on_ui(self, fn, timeout: float = 10.0):
        box: dict = {}
        done = threading.Event()
        self._queue.put((fn, box, done))
        if not done.wait(timeout):
            raise TimeoutError("Khata is busy")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    # ------------------------------------------------------------ helpers
    def _resolve_customer(self, svc, body: dict):
        customer_id = body.get("customer_id")
        if customer_id not in (None, ""):
            try:
                return svc.get_customer(int(customer_id))
            except (ValueError, NotFoundError):
                pass
        name = str(body.get("customer") or "").strip()
        if not name:
            raise ValidationError("Customer ka naam bolo")
        rows = svc.list_customers(name)
        if not rows:
            raise NotFoundError(f"Customer nahi mila: {name}")
        exact = [r for r in rows if r.customer.name.casefold() == name.casefold()]
        if len(exact) == 1:
            return exact[0].customer
        if len(rows) == 1:
            return rows[0].customer
        names = ", ".join(r.customer.name for r in rows[:6])
        raise ValidationError(f"Ek se zyada customer mile ({names}) — poora naam bolo")

    def _add_entry(self, svc, body: dict) -> dict:
        customer = self._resolve_customer(svc, body)
        txn_type = _type_from_text(body.get("type"))
        date = str(body.get("date") or "").strip() or svc.today().isoformat()
        mode = str(body.get("mode") or "pending").strip().casefold()
        direct = mode in ("direct", "abhi", "now", "instant")
        notes = str(body.get("notes") or "").strip()
        if "[phone]" not in notes:
            notes = (notes + " [phone]").strip()
        kwargs = dict(
            customer_id=customer.id,
            txn_type=txn_type,
            txn_date=date,
            item=body.get("item") or ("Cash" if txn_type == "JAMA" else "Goods"),
            amount=body.get("amount"),
            quantity=body.get("quantity"),
            rate=body.get("rate"),
            notes=notes,
            due_date=body.get("due_date"),
        )
        balance = svc.customer_balance(customer.id) + svc._pending_udhaar_total(customer.id)
        if direct:
            txn = svc.add_transaction(**kwargs)
            balance += txn.amount_paise if txn.txn_type == "UDHAAR" else -txn.amount_paise
            return {"mode": "direct", "entry": _txn_json(txn), "customer": customer.name,
                    "balance_paise": svc.customer_balance(customer.id)}
        pending = svc.add_pending_entry(**kwargs)
        balance += pending.amount_paise if pending.txn_type == "UDHAAR" else -pending.amount_paise
        return {"mode": "pending", "entry": _pending_json(pending), "customer": customer.name,
                "balance_paise": balance}

    def _ledger(self, svc, body_q: dict) -> dict:
        def one(key):
            value = body_q.get(key)
            if isinstance(value, list):
                return value[0] if value else ""
            return "" if value is None else value

        customer_id = one("customer_id")
        if not customer_id:
            customer = self._resolve_customer(svc, {"customer": one("customer")})
            customer_id = customer.id
        ledger = svc.ledger(int(customer_id))
        rows = []
        for row in ledger.rows[-40:]:
            t = row.txn
            rows.append({
                "id": t.id,
                "date": t.txn_date,
                "type": t.txn_type,
                "item": t.item,
                "quantity": t.quantity,
                "rate_paise": t.rate_paise,
                "amount_paise": t.amount_paise,
                "notes": t.notes or "",
                "due_date": t.due_date,
            })
        return {
            "customer": {"id": ledger.customer.id, "name": ledger.customer.name,
                         "mobile": ledger.customer.mobile or ""},
            "balance_paise": ledger.closing_balance,
            "total_udhaar_paise": ledger.total_udhaar,
            "total_jama_paise": ledger.total_jama,
            "rows": rows,
        }

    def _overdue(self, svc) -> dict:
        today = svc.today().isoformat()
        rows = svc.conn.execute(
            """SELECT c.id, c.name, c.mobile, SUM(t.amount_paise) AS due_paise, COUNT(t.id) AS cnt
                 FROM transactions t JOIN customers c ON c.id = t.customer_id
                WHERE t.txn_type = 'UDHAAR' AND t.due_date IS NOT NULL AND t.due_date < ?
                GROUP BY c.id ORDER BY due_paise DESC""", (today,)).fetchall()
        return {"overdue": [
            {"customer_id": r[0], "name": r[1], "mobile": r[2] or "",
             "due_paise": int(r[3] or 0), "entries": r[4]}
            for r in rows
        ]}

    def _top(self, svc, limit: int) -> dict:
        rows = svc.list_customers()
        rows = sorted(rows, key=lambda r: r.balance, reverse=True)
        rows = [r for r in rows if r.balance > 0][:limit]
        return {"top": [
            {"id": r.customer.id, "name": r.customer.name, "balance_paise": r.balance}
            for r in rows
        ]}

    def _report(self, svc, date_from: str, date_to: str) -> dict:
        reg = svc.register(date_from=date_from or None, date_to=date_to or None)
        return {
            "from": date_from or None,
            "to": date_to or None,
            "total_udhaar_paise": reg.total_udhaar,
            "total_jama_paise": reg.total_jama,
            "net_paise": reg.net,
            "entries": len(reg.rows),
            "rows": [
                {"customer": r.customer_name, "type": r.txn.txn_type, "date": r.txn.txn_date,
                 "item": r.txn.item, "amount_paise": r.txn.amount_paise}
                for r in reg.rows[-60:]
            ],
        }

    def _undo_last(self, svc) -> dict:
        pend = svc.list_pending()
        if pend:
            last = max(pend, key=lambda p: p.id)
            svc.delete_pending(last.id)
            return {"undone": "pending", "customer": last.customer_name, "item": last.item,
                    "amount_paise": last.amount_paise}
        row = svc.conn.execute(
            "SELECT id, customer_id, item, amount_paise, txn_type, created_at "
            "FROM transactions ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            raise ValidationError("Koi entry nahi hai undo karne ke liye")
        txn = svc.get_transaction(row[0])
        svc.delete_transaction(txn.id)
        return {"undone": "transaction", "customer_id": txn.customer_id, "item": txn.item,
                "amount_paise": txn.amount_paise}

    def _stats(self, svc) -> dict:
        pend = svc.list_pending()
        last = pend[-1] if pend else None
        return {
            "pending_count": len(pend),
            "last_pending": _pending_json(last) if last else None,
            "pin_set": bool(svc.get_setting("phone_pin", "")),
        }

    def _items(self, svc) -> dict:
        rows = svc.conn.execute(
            "SELECT item, rate_paise FROM transactions WHERE rate_paise IS NOT NULL ORDER BY id DESC LIMIT 500"
        ).fetchall()
        seen: dict[str, dict] = {}
        for item, rate in rows:
            key = (item or "").strip().casefold()
            if key and key not in seen:
                seen[key] = {"item": item.strip(), "rate_paise": int(rate or 0)}
            if len(seen) >= 60:
                break
        return {"items": list(seen.values())}

    def _aging(self, svc) -> dict:
        today = svc.today()
        rows = svc.conn.execute(
            "SELECT customer_id, txn_date, txn_type, amount_paise FROM transactions ORDER BY txn_date, id"
        ).fetchall()
        names = {r.customer.id: r.customer.name for r in svc.list_customers()}
        per: dict[int, list] = {}
        for cid, txn_date, txn_type, amount in rows:
            per.setdefault(cid, []).append((txn_date, txn_type, int(amount or 0)))
        totals = {"b0_30": 0, "b31_60": 0, "b61_plus": 0}
        customers = []
        for cid, entries in per.items():
            open_rows: list[list] = []
            for txn_date, txn_type, amount in entries:
                if txn_type == "UDHAAR":
                    open_rows.append([amount, txn_date])
                else:
                    pay = amount
                    for row in open_rows:
                        if pay <= 0:
                            break
                        take = min(row[0], pay)
                        row[0] -= take
                        pay -= take
            bucket = {"b0_30": 0, "b31_60": 0, "b61_plus": 0}
            total = 0
            for remaining, txn_date in open_rows:
                if remaining <= 0:
                    continue
                try:
                    age = (today - _date.fromisoformat(txn_date)).days
                except ValueError:
                    continue
                key = "b0_30" if age <= 30 else "b31_60" if age <= 60 else "b61_plus"
                bucket[key] += remaining
                total += remaining
            if total > 0:
                customers.append({"id": cid, "name": names.get(cid, "?"), "total_paise": total, **bucket})
                for key in bucket:
                    totals[key] += bucket[key]
        customers.sort(key=lambda c: c["total_paise"], reverse=True)
        return {"buckets": totals, "customers": customers[:60]}

    def _edit_pending(self, svc, pending_id: int, body: dict):
        old = svc.get_pending_entry(pending_id)
        amount = body.get("amount")
        if amount in (None, ""):
            amount = f"{old.amount_paise / 100:.2f}"
            quantity = None
            rate = None
        else:
            quantity = body.get("quantity")
            rate = body.get("rate")
        svc.delete_pending(pending_id)
        notes = (old.notes + " (edited)").strip()
        return svc.add_pending_entry(
            customer_id=old.customer_id,
            txn_type=old.txn_type,
            txn_date=old.txn_date,
            item=body.get("item") or old.item,
            amount=amount,
            quantity=quantity,
            rate=rate,
            notes=notes,
        )

    def _statement_pdf(self, svc, query: dict) -> tuple[bytes, str]:
        def one(key):
            value = query.get(key)
            if isinstance(value, list):
                return value[0] if value else ""
            return "" if value is None else value

        customer_id = one("customer_id")
        if not customer_id:
            customer = self._resolve_customer(svc, {"customer": one("customer")})
            customer_id = customer.id
        ledger = svc.ledger(int(customer_id))
        tmp = Path(tempfile.mkdtemp(prefix="vx7pdf-"))
        try:
            path = exports.export_statement_pdf(
                ledger, tmp / "statement.pdf",
                shop_name=svc.get_setting("shop_name", ""),
                shop_details=svc.get_setting("shop_details", ""))
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", ledger.customer.name) or "customer"
            return path.read_bytes(), f"statement_{safe}.pdf"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _register_pdf(self, svc, date_from: str, date_to: str) -> tuple[bytes, str]:
        register = svc.register(date_from=date_from or None, date_to=date_to or None)
        tmp = Path(tempfile.mkdtemp(prefix="vx7pdf-"))
        try:
            path = exports.export_register_pdf(
                register, tmp / "register.pdf",
                shop_name=svc.get_setting("shop_name", ""),
                customer_label="All customers")
            return path.read_bytes(), "register.pdf"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _shop(self, svc) -> dict:
        return {
            "shop_name": svc.get_setting("shop_name", "Khata"),
            "upi_id": svc.get_setting("upi_id", ""),
        }

    def _reminders(self, svc) -> dict:
        try:
            items = json.loads(svc.get_setting("phone_reminders", "[]"))
        except ValueError:
            items = []
        today = svc.today().isoformat()
        for item in items:
            item["due_today"] = item.get("date", "") <= today
        items.sort(key=lambda r: r.get("date", ""))
        return {"reminders": items}

    def _reminders_add(self, svc, body: dict) -> dict:
        try:
            items = json.loads(svc.get_setting("phone_reminders", "[]"))
        except ValueError:
            items = []
        name = str(body.get("customer") or "").strip()
        if not name:
            raise ValidationError("Customer ka naam bolo")
        entry = {
            "id": str(int(time.time() * 1000)),
            "customer": name,
            "date": str(body.get("date") or svc.today().isoformat()),
            "note": str(body.get("note") or ""),
        }
        items.append(entry)
        items = items[-300:]
        svc.set_setting("phone_reminders", json.dumps(items))
        return {"reminder": entry}

    def _reminders_delete(self, svc, reminder_id: str) -> dict:
        try:
            items = json.loads(svc.get_setting("phone_reminders", "[]"))
        except ValueError:
            items = []
        kept = [r for r in items if str(r.get("id")) != str(reminder_id)]
        svc.set_setting("phone_reminders", json.dumps(kept))
        return {"deleted": len(items) - len(kept)}

    def _trend(self, svc, days: int) -> dict:
        rows = svc.conn.execute(
            """SELECT txn_date,
                      COALESCE(SUM(CASE WHEN txn_type='UDHAAR' THEN amount_paise END), 0),
                      COALESCE(SUM(CASE WHEN txn_type='JAMA' THEN amount_paise END), 0)
                 FROM transactions GROUP BY txn_date ORDER BY txn_date DESC LIMIT ?""",
            (max(1, min(60, days)),)).fetchall()
        days_list = [{"date": r[0], "udhaar_paise": int(r[1]), "jama_paise": int(r[2])}
                     for r in reversed(rows)]
        return {"days": days_list}

    def _items_report(self, svc, days: int) -> dict:
        rows = svc.conn.execute(
            """SELECT item, COUNT(*), COALESCE(SUM(amount_paise), 0)
                 FROM transactions
                WHERE txn_date >= date('now', 'localtime', ?)
                GROUP BY lower(item) ORDER BY 3 DESC LIMIT 25""",
            (f"-{max(1, min(365, days))} days",)).fetchall()
        return {"items": [{"item": r[0], "count": r[1], "amount_paise": int(r[2])} for r in rows]}

    def _expenses(self, svc, day: str) -> dict:
        try:
            items = json.loads(svc.get_setting("phone_expenses", "[]"))
        except ValueError:
            items = []
        day = day or svc.today().isoformat()
        today = [r for r in items if r.get("date") == day]
        return {"date": day, "expenses": today,
                "total_paise": sum(int(r.get("amount_paise") or 0) for r in today)}

    def _expenses_add(self, svc, body: dict) -> dict:
        try:
            items = json.loads(svc.get_setting("phone_expenses", "[]"))
        except ValueError:
            items = []
        item = str(body.get("item") or "").strip()
        if not item:
            raise ValidationError("Kharcha kya hai? (jaise: bijli bill)")
        try:
            amount_paise = int(round(float(body.get("amount") or 0) * 100))
        except (TypeError, ValueError):
            raise ValidationError("Amount sahi likho") from None
        if amount_paise <= 0:
            raise ValidationError("Amount 0 se zyada do")
        entry = {
            "id": str(int(time.time() * 1000)),
            "date": str(body.get("date") or svc.today().isoformat()),
            "item": item,
            "amount_paise": amount_paise,
        }
        items.append(entry)
        svc.set_setting("phone_expenses", json.dumps(items[-2000:]))
        return {"expense": entry}

    def _backup_bytes(self, svc) -> tuple[bytes, str]:
        tmp = Path(tempfile.mkdtemp(prefix="vx7backup-"))
        try:
            target = tmp / "khata-backup.db"
            dest = sqlite3.connect(str(target))
            try:
                svc.conn.backup(dest)
            finally:
                dest.close()
            stamp = time.strftime("%Y-%m-%d")
            return target.read_bytes(), f"khata-backup-{stamp}.db"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ------------------------------------------------------------ HTTP
    def _handler_class(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "VX7KhataBridge/1.0"

            def log_message(self, *_args):  # silence per-request logging
                pass

            def _send(self, status: int, payload: dict) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_bytes(self, status: int, data: bytes, content_type: str, filename: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.end_headers()
                self.wfile.write(data)

            def _auth(self, query: dict) -> bool:
                header = self.headers.get("X-Bridge-Token") or ""
                supplied = header or (query.get("t") or [""])[0]
                return bool(supplied) and hmac.compare_digest(supplied, bridge.token)

            def _body(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > _MAX_BODY:
                    return {}
                try:
                    return json.loads(self.rfile.read(length).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return {}

            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if not self._auth(query):
                    return self._send(401, {"ok": False, "error": "unauthorized"})
                try:
                    if parsed.path == "/khata/health":
                        return self._send(200, {"ok": True, "port": bridge.port})
                    if parsed.path == "/khata/customers":
                        search = (query.get("search") or [""])[0]
                        rows = bridge._run_on_ui(lambda svc: svc.list_customers(search))
                        return self._send(200, {"ok": True,
                                                "customers": [_customer_json(r) for r in rows]})
                    if parsed.path == "/khata/summary":
                        day = (query.get("date") or [""])[0] or None
                        summary = bridge._run_on_ui(lambda svc: svc.daily_summary(day))
                        return self._send(200, {
                            "ok": True,
                            "day": summary.day,
                            "total_jama_paise": summary.total_jama,
                            "total_udhaar_paise": summary.total_udhaar,
                            "net_paise": summary.net_difference,
                            "transaction_count": summary.transaction_count,
                            "customer_count": summary.customer_count,
                            "rows": [
                                {
                                    "customer": r.customer_name,
                                    "type": r.txn_type,
                                    "amount_paise": r.amount_paise,
                                    "item": r.item,
                                    "date": r.txn_date,
                                    "time": r.time,
                                }
                                for r in summary.rows
                            ],
                        })
                    if parsed.path == "/khata/pending":
                        rows = bridge._run_on_ui(lambda svc: svc.list_pending())
                        return self._send(200, {"ok": True,
                                                "pending": [_pending_json(p) for p in rows]})
                    if parsed.path == "/khata/overdue":
                        return self._send(200, {"ok": True, **bridge._run_on_ui(bridge._overdue)})
                    if parsed.path == "/khata/top":
                        try:
                            limit = max(1, min(20, int((query.get("limit") or ["8"])[0] or 8)))
                        except ValueError:
                            limit = 8
                        return self._send(200, {"ok": True,
                                                **bridge._run_on_ui(lambda svc: bridge._top(svc, limit))})
                    if parsed.path == "/khata/ledger":
                        return self._send(200, {"ok": True,
                                                **bridge._run_on_ui(lambda svc: bridge._ledger(svc, query))})
                    if parsed.path == "/khata/report":
                        date_from = (query.get("from") or [""])[0]
                        date_to = (query.get("to") or [""])[0]
                        return self._send(200, {"ok": True, **bridge._run_on_ui(
                            lambda svc: bridge._report(svc, date_from, date_to))})
                    if parsed.path == "/khata/stats":
                        return self._send(200, {"ok": True, **bridge._run_on_ui(bridge._stats)})
                    if parsed.path == "/khata/items":
                        return self._send(200, {"ok": True, **bridge._run_on_ui(bridge._items)})
                    if parsed.path == "/khata/aging":
                        return self._send(200, {"ok": True, **bridge._run_on_ui(bridge._aging)})
                    if parsed.path == "/khata/ledger.pdf":
                        data, filename = bridge._run_on_ui(lambda svc: bridge._statement_pdf(svc, query), timeout=25)
                        return self._send_bytes(200, data, "application/pdf", filename)
                    if parsed.path == "/khata/report.pdf":
                        date_from = (query.get("from") or [""])[0]
                        date_to = (query.get("to") or [""])[0]
                        data, filename = bridge._run_on_ui(
                            lambda svc: bridge._register_pdf(svc, date_from, date_to), timeout=25)
                        return self._send_bytes(200, data, "application/pdf", filename)
                    if parsed.path == "/khata/shop":
                        return self._send(200, {"ok": True, **bridge._run_on_ui(bridge._shop)})
                    if parsed.path == "/khata/reminders":
                        return self._send(200, {"ok": True, **bridge._run_on_ui(bridge._reminders)})
                    if parsed.path == "/khata/trend":
                        try:
                            days = int((query.get("days") or ["7"])[0] or 7)
                        except ValueError:
                            days = 7
                        return self._send(200, {"ok": True,
                                                **bridge._run_on_ui(lambda svc: bridge._trend(svc, days))})
                    if parsed.path == "/khata/items-report":
                        try:
                            days = int((query.get("days") or ["30"])[0] or 30)
                        except ValueError:
                            days = 30
                        return self._send(200, {"ok": True, **bridge._run_on_ui(
                            lambda svc: bridge._items_report(svc, days))})
                    if parsed.path == "/khata/expenses":
                        day = (query.get("date") or [""])[0]
                        return self._send(200, {"ok": True,
                                                **bridge._run_on_ui(lambda svc: bridge._expenses(svc, day))})
                    if parsed.path == "/khata/backup":
                        data, filename = bridge._run_on_ui(bridge._backup_bytes, timeout=30)
                        return self._send_bytes(200, data, "application/x-sqlite3", filename)
                    return self._send(404, {"ok": False, "error": "unknown path"})
                except KhataError as exc:
                    return self._send(400, {"ok": False, "error": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    return self._send(500, {"ok": False, "error": str(exc)})

            def do_POST(self):  # noqa: N802
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if not self._auth(query):
                    return self._send(401, {"ok": False, "error": "unauthorized"})
                body = self._body()
                try:
                    if parsed.path == "/khata/entries":
                        result = bridge._run_on_ui(lambda svc: bridge._add_entry(svc, body))
                        label = "Daily Entry me daal diya (24h me save hoga)" if result["mode"] == "pending" \
                            else "Seedha ledger me save ho gaya"
                        return self._send(200, {"ok": True, "message": label, **result})
                    match = re.fullmatch(r"/khata/pending/(\d+)/(post|delete|edit)", parsed.path)
                    if match:
                        pending_id = int(match.group(1))
                        if match.group(2) == "edit":
                            entry = bridge._run_on_ui(lambda svc: bridge._edit_pending(svc, pending_id, body))
                            return self._send(200, {"ok": True, "entry": _pending_json(entry),
                                                    "message": "Pending entry update ho gayi"})
                        if match.group(2) == "post":
                            txn = bridge._run_on_ui(lambda svc: svc.post_pending(pending_id))
                            return self._send(200, {"ok": True, "transaction": _txn_json(txn),
                                                    "message": "Entry ledger me save ho gayi"})
                        bridge._run_on_ui(lambda svc: svc.delete_pending(pending_id))
                        return self._send(200, {"ok": True, "message": "Pending entry delete ho gayi"})
                    if parsed.path == "/khata/customers":
                        customer = bridge._run_on_ui(lambda svc: svc.add_customer(
                            name=body.get("name") or "",
                            mobile=body.get("mobile") or "",
                            address=body.get("address") or "",
                            notes=body.get("notes") or "",
                            credit_limit=body.get("credit_limit") or "",
                        ))
                        return self._send(200, {"ok": True, "id": customer.id, "name": customer.name})
                    if parsed.path == "/khata/undo-last":
                        result = bridge._run_on_ui(bridge._undo_last)
                        return self._send(200, {"ok": True, "message": "Last entry undo ho gayi", **result})
                    if parsed.path == "/khata/reminders":
                        result = bridge._run_on_ui(lambda svc: bridge._reminders_add(svc, body))
                        return self._send(200, {"ok": True, **result})
                    if parsed.path == "/khata/reminders/delete":
                        result = bridge._run_on_ui(
                            lambda svc: bridge._reminders_delete(svc, str(body.get("id") or "")))
                        return self._send(200, {"ok": True, **result})
                    if parsed.path == "/khata/expenses":
                        result = bridge._run_on_ui(lambda svc: bridge._expenses_add(svc, body))
                        return self._send(200, {"ok": True, **result})
                    if parsed.path == "/khata/pin":
                        pin = str(body.get("pin") or "").strip()
                        if pin and not re.fullmatch(r"\d{4,8}", pin):
                            raise ValidationError("PIN 4-8 digits ka rakho")
                        existing = bridge._run_on_ui(lambda svc: svc.get_setting("phone_pin", ""))
                        if pin and existing:
                            old = str(body.get("old_pin") or "").strip()
                            if not (old and hmac.compare_digest(old.encode(), existing.encode())):
                                return self._send(403, {"ok": False, "needs_old": True,
                                                        "error": "Purana PIN galat hai"})
                        bridge._run_on_ui(lambda svc: svc.set_setting("phone_pin", pin))
                        return self._send(200, {"ok": True, "pin_set": bool(pin)})
                    if parsed.path == "/khata/pin/verify":
                        pin = str(body.get("pin") or "").strip()
                        expected = bridge._run_on_ui(lambda svc: svc.get_setting("phone_pin", ""))
                        match = bool(expected) and hmac.compare_digest(pin.encode(), expected.encode())
                        return self._send(200, {"ok": True, "match": match})
                    return self._send(404, {"ok": False, "error": "unknown path"})
                except ValidationError as exc:
                    return self._send(400, {"ok": False, "error": str(exc)})
                except NotFoundError as exc:
                    return self._send(404, {"ok": False, "error": str(exc)})
                except (DuplicateEntryError, CreditLimitError) as exc:
                    return self._send(409, {"ok": False, "error": str(exc)})
                except KhataError as exc:
                    return self._send(400, {"ok": False, "error": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    return self._send(500, {"ok": False, "error": str(exc)})

        return Handler
