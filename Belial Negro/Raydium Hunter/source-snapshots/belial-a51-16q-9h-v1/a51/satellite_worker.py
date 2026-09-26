"""Read-only external satellites. Never imported by PAPER/strategy modules."""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import math
import re
import time
import urllib.request
from pathlib import Path

from a51.common import SYMBOLS, Stop, atomic, heartbeat, now_ms, session_expired
from a51.journal import Journal

POLL_SECONDS = 3.0
MAX_RESPONSE_BYTES = 24 << 20
USER_AGENT = "Belial-A51-SATELLITE-READ-ONLY/1"
FRESH_MS = 10_000
DIRECTION_THRESHOLD_BPS = 2.0
JOURNAL_EVERY_MS = 30_000

BINANCE_BOOK = "https://fapi.binance.com/fapi/v1/ticker/bookTicker"
BINANCE_PREMIUM = "https://fapi.binance.com/fapi/v1/premiumIndex"
BYBIT_TICKERS = "https://api.bybit.com/v5/market/tickers?category=linear"
KRAKEN_TICKERS = "https://futures.kraken.com/derivatives/api/v3/tickers"
VENUES = ("binance", "bybit", "kraken")


def number(value):
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def positive(value):
    value = number(value)
    return value if value is not None and value > 0 else None


def resolve_market(name):
    if not isinstance(name, str) or not name:
        return None
    clean = re.sub(r"[^A-Z0-9]", "", name.upper())
    for prefix in ("PERP", "PF", "PI"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
    for suffix in ("PERPETUAL", "PERP", "USDT", "USD"):
        if clean.endswith(suffix):
            clean = clean[:-len(suffix)]
            break
    for symbol in sorted(SYMBOLS, key=len, reverse=True):
        if clean == symbol:
            return symbol, 1
        if clean in ("1000" + symbol, "K" + symbol):
            return symbol, 1000
        if clean == "1000000" + symbol:
            return symbol, 1_000_000
    return None


def normalized_row(venue, market, bid, ask, at_ms, **extra):
    resolved = resolve_market(market)
    if not resolved:
        return None
    symbol, multiplier = resolved
    bid = positive(bid)
    ask = positive(ask)
    if bid is None or ask is None or bid >= ask:
        return None
    bid /= multiplier
    ask /= multiplier
    mid = (bid + ask) / 2
    row = {
        "venue": venue,
        "symbol": symbol,
        "market": market,
        "multiplier": multiplier,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_bps": (ask - bid) / mid * 10000,
        "at_ms": int(at_ms),
        "orders": False,
        "shadow_only": True,
    }
    row.update(extra)
    return row


def choose(rows):
    chosen = {}
    for row in rows:
        if not row:
            continue
        current = chosen.get(row["symbol"])
        score = (
            0 if row["multiplier"] == 1 else 1,
            row.get("spread_bps", float("inf")),
        )
        old_score = (
            0 if current and current["multiplier"] == 1 else 1,
            current.get("spread_bps", float("inf")) if current else float("inf"),
        )
        if current is None or score < old_score:
            chosen[row["symbol"]] = row
    return chosen


def fetch_json(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("SATELLITE_RESPONSE_TOO_LARGE")
    return json.loads(raw)


def binance_snapshot(at_ms):
    books = fetch_json(BINANCE_BOOK)
    premium = fetch_json(BINANCE_PREMIUM)
    books = [books] if isinstance(books, dict) else books
    premium = [premium] if isinstance(premium, dict) else premium
    if not isinstance(books, list) or not isinstance(premium, list):
        raise ValueError("BINANCE_SCHEMA")
    premium_map = {
        row.get("symbol"): row
        for row in premium
        if isinstance(row, dict) and row.get("symbol")
    }
    rows = []
    for row in books:
        if not isinstance(row, dict):
            continue
        market = row.get("symbol")
        meta = premium_map.get(market, {})
        multiplier = (resolve_market(market) or (None, 1))[1]
        mark = positive(meta.get("markPrice"))
        index = positive(meta.get("indexPrice"))
        rows.append(normalized_row(
            "binance",
            market,
            row.get("bidPrice"),
            row.get("askPrice"),
            at_ms,
            mark_price=mark / multiplier if mark else None,
            index_price=index / multiplier if index else None,
            funding_rate=number(meta.get("lastFundingRate")),
            open_interest=None,
            source_time_ms=int(number(row.get("time")) or at_ms),
        ))
    return choose(rows)


def bybit_snapshot(at_ms):
    document = fetch_json(BYBIT_TICKERS)
    rows = (((document or {}).get("result") or {}).get("list") or [])
    if not isinstance(rows, list):
        raise ValueError("BYBIT_SCHEMA")
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        market = row.get("symbol")
        multiplier = (resolve_market(market) or (None, 1))[1]
        mark = positive(row.get("markPrice"))
        index = positive(row.get("indexPrice"))
        parsed.append(normalized_row(
            "bybit",
            market,
            row.get("bid1Price"),
            row.get("ask1Price"),
            at_ms,
            mark_price=mark / multiplier if mark else None,
            index_price=index / multiplier if index else None,
            funding_rate=number(row.get("fundingRate")),
            open_interest=number(row.get("openInterest")),
            source_time_ms=int(number((document or {}).get("time")) or at_ms),
        ))
    return choose(parsed)


def kraken_snapshot(at_ms):
    document = fetch_json(KRAKEN_TICKERS)
    rows = (document or {}).get("tickers") or []
    if not isinstance(rows, list):
        raise ValueError("KRAKEN_SCHEMA")
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        market = row.get("symbol") or row.get("pair")
        multiplier = (resolve_market(market) or (None, 1))[1]
        mark = positive(row.get("markPrice"))
        index = positive(row.get("index"))
        parsed.append(normalized_row(
            "kraken",
            market,
            row.get("bid"),
            row.get("ask"),
            at_ms,
            mark_price=mark / multiplier if mark else None,
            index_price=index / multiplier if index else None,
            funding_rate=number(
                row.get("fundingRate")
                if row.get("fundingRate") is not None
                else row.get("fundingRatePrediction")
            ),
            open_interest=number(row.get("openInterest")),
            source_time_ms=at_ms,
        ))
    return choose(parsed)


FETCHERS = {
    "binance": binance_snapshot,
    "bybit": bybit_snapshot,
    "kraken": kraken_snapshot,
}


def history_return(history, venue, symbol, at_ms, mid):
    values = history[(venue, symbol)]
    values.append((at_ms, mid))
    while values and at_ms - values[0][0] > 30_000:
        values.popleft()
    reference = None
    for ts, price in reversed(values):
        if ts <= at_ms - 8_000:
            reference = price
            break
    if reference is None or reference <= 0:
        return None
    return (mid / reference - 1) * 10000


def direction(return_bps):
    if return_bps is None:
        return "UNKNOWN"
    if return_bps >= DIRECTION_THRESHOLD_BPS:
        return "UP"
    if return_bps <= -DIRECTION_THRESHOLD_BPS:
        return "DOWN"
    return "FLAT"


def aggregate_symbol(symbol, venue_rows, at_ms):
    rows = {}
    directions = []
    mids = []
    for venue in VENUES:
        row = venue_rows.get(venue, {}).get(symbol)
        if not row:
            rows[venue] = {
                "venue": venue,
                "supported": False,
                "fresh": False,
                "direction": "UNKNOWN",
                "orders": False,
                "shadow_only": True,
            }
            continue
        item = dict(row)
        item["age_ms"] = max(0, at_ms - int(item.get("at_ms", at_ms)))
        item["fresh"] = item["age_ms"] <= FRESH_MS
        item["supported"] = True
        rows[venue] = item
        if item["fresh"]:
            mids.append(item["mid"])
            if item.get("direction") in ("UP", "DOWN"):
                directions.append(item["direction"])

    up = directions.count("UP")
    down = directions.count("DOWN")
    if max(up, down) >= 2:
        state = "CONFIRMED"
        consensus = "UP" if up > down else "DOWN"
        agreement = max(up, down)
    elif up and down:
        state = "DIVERGENT"
        consensus = "MIXED"
        agreement = max(up, down)
    else:
        state = "NEUTRAL"
        consensus = directions[0] if directions else "FLAT"
        agreement = max(up, down)

    divergence = None
    if len(mids) >= 2:
        center = sum(mids) / len(mids)
        if center > 0:
            divergence = (max(mids) - min(mids)) / center * 10000

    return {
        "symbol": symbol,
        "state": state,
        "direction": consensus,
        "agreement_count": agreement,
        "fresh_count": sum(bool(row.get("fresh")) for row in rows.values()),
        "price_divergence_bps": divergence,
        "venues": rows,
        "funding_comparison": "RAW_RATES_NOT_NORMALIZED_ACROSS_INTERVALS",
        "open_interest_comparison": "RAW_VENUE_UNITS_NOT_AGGREGATED",
        "orders": False,
        "shadow_only": True,
        "affects_trading": False,
    }


def collect(history, last_good, executor):
    at_ms = now_ms()
    results = {}
    venue_status = {}
    futures = {
        venue: executor.submit(fetcher, at_ms)
        for venue, fetcher in FETCHERS.items()
    }
    for venue, future in futures.items():
        try:
            result = future.result(timeout=12)
            last_good[venue] = result
            results[venue] = result
            venue_status[venue] = {
                "state": "ACTIVE",
                "symbols": len(result),
                "error": None,
                "last_success_ms": at_ms,
            }
        except Exception as exc:
            previous = last_good.get(venue, {})
            results[venue] = previous
            last_success = max(
                [int(row.get("at_ms", 0)) for row in previous.values()] or [0]
            )
            venue_status[venue] = {
                "state": "DEGRADED",
                "symbols": len(previous),
                "error": str(exc)[:500],
                "last_success_ms": last_success or None,
            }

    for venue, rows in results.items():
        for symbol, row in rows.items():
            return_bps = history_return(history, venue, symbol, at_ms, row["mid"])
            row["return_10s_bps"] = return_bps
            row["direction"] = direction(return_bps)

    by_symbol = {
        symbol: aggregate_symbol(symbol, results, at_ms)
        for symbol in SYMBOLS
    }
    active = sum(row["state"] == "ACTIVE" for row in venue_status.values())
    state = "ACTIVE" if active == len(VENUES) else "DEGRADED" if active else "UNAVAILABLE"
    return {
        "schema": "belial_a51_satellites_v1",
        "at_ms": at_ms,
        "state": state,
        "venues": venue_status,
        "by_symbol": by_symbol,
        "poll_seconds": POLL_SECONDS,
        "shadow_only": True,
        "orders": False,
        "affects_trading": False,
        "not_a_trade_permission": True,
    }


def self_test():
    assert resolve_market("WIFUSDT") == ("WIF", 1)
    assert resolve_market("1000BONKUSDT") == ("BONK", 1000)
    assert resolve_market("PF_WIFUSD") == ("WIF", 1)
    assert resolve_market("DRIFTUSDT") is None
    assert direction(3) == "UP"
    assert direction(-3) == "DOWN"
    assert direction(0.2) == "FLAT"
    row = normalized_row("x", "WIFUSDT", "1", "1.01", 10)
    assert row and row["symbol"] == "WIF"
    return True


def run(session, once=False):
    session = Path(session)
    out = session / "satellites"
    out.mkdir(parents=True, exist_ok=True)
    stop = Stop().install()
    history = collections.defaultdict(collections.deque)
    last_good = {}
    journal = Journal(out / "events.jsonl")
    last_journal = 0

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            while not stop.value and not session_expired(session):
                started = time.monotonic()
                state = collect(history, last_good, executor)
                atomic(out / "state.json", state)
                heartbeat(
                    out / "status.json",
                    state["state"],
                    venues=state["venues"],
                    shadow_only=True,
                    orders=False,
                    affects_trading=False,
                )
                if state["at_ms"] - last_journal >= JOURNAL_EVERY_MS:
                    journal.append({
                        "kind": "SATELLITE_SNAPSHOT",
                        "body": state,
                        "orders": False,
                        "shadow_only": True,
                    })
                    last_journal = state["at_ms"]
                if once:
                    break
                time.sleep(max(0.05, POLL_SECONDS - (time.monotonic() - started)))
    finally:
        journal.close()
        heartbeat(
            out / "status.json",
            "STOPPED",
            shadow_only=True,
            orders=False,
            affects_trading=False,
        )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("A51_SATELLITE_SELF_TEST=PASS")
        raise SystemExit(0)
    if args.session is None:
        raise SystemExit("--session required")
    raise SystemExit(run(args.session, args.once))
