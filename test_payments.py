"""test_payments.py — смоук-тесты безопасности вебхука Lava и идемпотентности зачислений."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import unittest

# Изолированное окружение: своя БД, пустые токены, включён секрет
_TMP = tempfile.mkdtemp(prefix="lotm_pay_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_bot.db")
os.environ["FSM_DB_PATH"] = os.path.join(_TMP, "test_fsm.db")
os.environ["BOT_TOKEN"] = "test:token"
os.environ["LLM_API_KEY"] = ""
os.environ["LAVA_WEBHOOK_SECRET"] = "s3cret-key-for-tests"
os.environ["DEV_MODE"] = "0"

import config  # noqa: E402
import db  # noqa: E402
import web_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SECRET = config.LAVA_WEBHOOK_SECRET
USER_ID = 777000
H = {"Content-Type": "application/json"}


def _signed(body: bytes) -> dict:
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {**H, "X-Signature": sig}


def _event(event: str, contract_id: str) -> dict:
    return {
        "event": event,
        "contract_id": contract_id,
        "product_id": "vip_450",
        "client_id": str(USER_ID),
    }


class LavaWebhookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await db.init_db()
        await db.get_character(USER_ID)  # создаёт персонажа + story_session

    def _post(self, event: dict, signed: bool = True, headers: dict | None = None):
        raw = json.dumps(event).encode()
        hdr = _signed(raw) if signed else (headers or H)
        with TestClient(web_app.app) as c:
            return c.post("/api/webhook/lava", content=raw, headers=hdr)

    async def _balance(self) -> tuple[int, int]:
        """(vip_until, fate_tokens) на текущий момент."""
        s = await db.get_story_session(USER_ID)
        return int(s.get("vip_until") or 0), int(s.get("fate_tokens") or 0)

    # ---------- 1. Платёж без подписи -> 403 ----------
    def test_unsigned_payment_rejected(self):
        r = self._post(_event("payment.success", "c-1"), signed=False)
        self.assertEqual(r.status_code, 403, "Вебхук без подписи должен получить 403")
        self.assertEqual(r.json().get("reason"), "bad_signature")

    # ---------- 2. Платёж с неверной подписью -> 403 ----------
    def test_wrong_signature_rejected(self):
        r = self._post(_event("payment.success", "c-2"), signed=False, headers={**H, "X-Signature": "deadbeef"})
        self.assertEqual(r.status_code, 403, "Вебхук с неверной подписью должен получить 403")
        self.assertEqual(r.json().get("reason"), "bad_signature")

    # ---------- 3. Подписанный payment.success зачисляет VIP ----------
    async def test_signed_payment_credits_vip(self):
        vip_before, tok_before = await self._balance()
        r = self._post(_event("payment.success", "c-3"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json().get("action"), "credited")
        vip_after, tok_after = await self._balance()
        self.assertGreater(vip_after, vip_before, "VIP должен быть зачислен (vip_until увеличился)")
        self.assertEqual(tok_after, tok_before + 3, "Тариф vip_450 даёт ровно +3 Печати Судьбы")

    # ---------- 4. Повторный вебхук (тот же contract_id) не задваивает ----------
    async def test_duplicate_webhook_ignored(self):
        vip0, tok0 = await self._balance()
        r1 = self._post(_event("payment.success", "c-4"))
        r2 = self._post(_event("payment.success", "c-4"))
        self.assertEqual(r1.json().get("action"), "credited", r1.text)
        self.assertEqual(r2.json().get("action"), "duplicate_ignored", r2.text)
        vip1, tok1 = await self._balance()
        self.assertEqual(tok1, tok0 + 3, "Жетоны судьбы должны быть зачислены ровно один раз")
        self.assertGreater(vip1, vip0)
        # Третий дубль тоже игнорируется, ничего не меняется
        r3 = self._post(_event("payment.success", "c-4"))
        self.assertEqual(r3.json().get("action"), "duplicate_ignored")
        vip2, tok2 = await self._balance()
        self.assertEqual((vip2, tok2), (vip1, tok1), "Повторные дубли не должны ничего менять")

    # ---------- 5. Подписанное событие cancelled — ок ----------
    async def test_cancelled_event_still_works(self):
        r = self._post({"event": "subscription.cancelled", "client_id": str(USER_ID)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("action"), "cancelled_logged")

    # ---------- 6. Неплатёжное событие без секрета вообще — работает ----------
    def test_nonpayment_event_without_secret_ok(self):
        old = config.LAVA_WEBHOOK_SECRET
        config.LAVA_WEBHOOK_SECRET = ""
        try:
            with TestClient(web_app.app) as c:
                r = c.post(
                    "/api/webhook/lava",
                    json={"event": "subscription.cancelled", "client_id": str(USER_ID)},
                )
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json().get("action"), "cancelled_logged")
        finally:
            config.LAVA_WEBHOOK_SECRET = old

    # ---------- 7. Платёж при НЕнастроенном секрете -> 503 (fail-closed) ----------
    def test_payment_blocked_without_secret(self):
        old = config.LAVA_WEBHOOK_SECRET
        config.LAVA_WEBHOOK_SECRET = ""
        try:
            with TestClient(web_app.app) as c:
                r = c.post(
                    "/api/webhook/lava",
                    json={"event": "payment.success", "client_id": str(USER_ID), "contract_id": "c-6"},
                )
            self.assertEqual(r.status_code, 503, "Без секрета зачисление должно быть запрещено")
            self.assertEqual(r.json().get("reason"), "secret_not_configured")
        finally:
            config.LAVA_WEBHOOK_SECRET = old


if __name__ == "__main__":
    unittest.main()
