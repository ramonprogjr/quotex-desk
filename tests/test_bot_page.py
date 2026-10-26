"""Piloto da Quotex contra uma página falsa que imita o layout real (sem rede, sem login)."""
import asyncio
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.async_api")
MOCK = (Path(__file__).parent / "fixtures" / "quotex_mock.html").resolve().as_uri()


async def _with_page(fn):
    async with pw.async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="chrome", headless=True)
        except Exception:  # noqa: BLE001
            try:
                browser = await p.chromium.launch(headless=True)
            except Exception:  # noqa: BLE001
                pytest.skip("nenhum navegador disponível para o Playwright")
        page = await browser.new_page(viewport={"width": 1600, "height": 900})
        await page.goto(MOCK)
        try:
            return await fn(page)
        finally:
            await browser.close()


def run(fn):
    return asyncio.run(_with_page(fn))


def test_reads_demo_account_and_balance():
    from backend.bot.quotex import QuotexPage

    acc = run(lambda page: QuotexPage(page).account())
    assert acc["type"] == "DEMO" and acc["balance"] == 10000.0


def test_detects_real_account():
    from backend.bot.quotex import QuotexPage

    async def go(page):
        await page.evaluate("document.getElementById('acctType').textContent = 'CONTA REAL'")
        return await QuotexPage(page).account()

    assert run(go)["type"] == "REAL"


def test_self_check_finds_everything():
    from backend.bot.quotex import QuotexPage

    d = run(lambda page: QuotexPage(page).self_check())
    assert d["ok"], d["checks"]


def test_selects_non_otc_pair_and_places_order():
    from backend.bot.quotex import QuotexPage

    async def go(page):
        q = QuotexPage(page)
        assert await q.select_asset("EUR/USD")
        cur = await q.current_asset()
        assert cur == {"name": "EUR/USD", "payout": 0.82}
        assert await q.set_duration(5)
        assert await q.set_amount(2)
        bal0 = (await q.account())["balance"]
        assert await q.click_direction("PUT")
        orders = await page.evaluate("window.orders")
        bal1 = (await q.account())["balance"]
        return orders, bal0, bal1

    orders, bal0, bal1 = run(go)
    assert orders == [{"dir": "PUT", "amt": 2, "tempo": "00:05:00", "asset": "EUR/USD"}]
    assert bal1 == bal0 - 2


def test_refuses_pair_only_available_as_otc():
    from backend.bot.quotex import QuotexPage

    assert run(lambda page: QuotexPage(page).select_asset("USD/JPY")) is False


def test_account_needs_text_and_url_to_agree():
    from backend.bot.quotex import reconcile_account

    assert reconcile_account("DEMO", "https://quotex.com/pt/demo-trade") == "DEMO"
    assert reconcile_account("REAL", "https://quotex.com/pt/trade") == "REAL"
    # divergência ou página fora da sala de operações: conta não identificada (robô não opera)
    assert reconcile_account("DEMO", "https://quotex.com/pt/trade") is None
    assert reconcile_account("REAL", "https://quotex.com/pt/demo-trade") is None
    assert reconcile_account("DEMO", "https://quotex.com/pt/sign-in") is None
    assert reconcile_account(None, "https://quotex.com/pt/demo-trade") is None
    # página falsa dos testes (fora da Quotex): vale o texto
    assert reconcile_account("DEMO", "file:///C:/tests/fixtures/quotex_mock.html") == "DEMO"
    assert reconcile_account("DEMO", "https://quotex.com.evil.example/pt/demo-trade") == "DEMO"  # não é a Quotex: só texto


def test_parse_money_formats():
    from backend.bot.quotex import parse_money

    assert parse_money("Conta demo $10,000.00") == 10000.0
    assert parse_money("R$ 1.234,56") == 1234.56
    assert parse_money("sem saldo") is None
