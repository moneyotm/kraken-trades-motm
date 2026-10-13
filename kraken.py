#!/usr/bin/env python3
"""
Kraken order-management CLI for one or two Kraken accounts.

Dry-run is always the default. --live requires typing CONFIRM.
See CLAUDE.md for the standard workflow and safety rules, and SETUP.md
for first-time setup.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_EVEN
from pathlib import Path
import warnings

# macOS system Python links LibreSSL; urllib3 warns about it on every import.
# Harmless for HTTPS to Kraken, so keep it out of the output.
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

import krakenex
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
ACCOUNTS_FILE = SCRIPT_DIR / "accounts.json"
LOG_FILE = SCRIPT_DIR / "logs" / "trade_log.jsonl"

# Kraken AddOrder parameters we allow in order files. Anything else aborts —
# a typo like "pricee" must never be silently dropped on the way to the API.
ALLOWED_ORDER_KEYS = {
    "label", "pair", "type", "ordertype", "price", "price2",
    "volume", "displayvol", "oflags", "trigger", "timeinforce",
}
REQUIRED_ORDER_KEYS = {"pair", "type", "ordertype", "volume"}

# ordertype -> (price fields required, price fields forbidden)
ORDERTYPE_PRICES = {
    "market":              (set(), {"price", "price2"}),
    "limit":               ({"price"}, {"price2"}),
    "stop-loss":           ({"price"}, {"price2"}),
    "take-profit":         ({"price"}, {"price2"}),
    "trailing-stop":       ({"price"}, {"price2"}),
    "stop-loss-limit":     ({"price", "price2"}, set()),
    "take-profit-limit":   ({"price", "price2"}, set()),
    "trailing-stop-limit": ({"price", "price2"}, set()),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _abort(msg: str) -> None:
    print(f"\nABORTED: {msg}")
    sys.exit(1)


def _assert_no_error(resp: dict, endpoint: str) -> None:
    if resp.get("error"):
        _abort(f"Kraken {endpoint} returned error: {resp['error']}")


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

def load_accounts() -> dict:
    if not ACCOUNTS_FILE.exists():
        _abort(f"accounts.json not found at {ACCOUNTS_FILE}")
    with open(ACCOUNTS_FILE) as f:
        return json.load(f)["accounts"]


def get_api(account_name: str, withdraw: bool = False) -> krakenex.API:
    """Build an authenticated API client for a named account. Prints a banner
    so it is always visible which account a private call is about to touch.
    withdraw=True uses the account's separate withdraw-only key instead."""
    accounts = load_accounts()
    if account_name not in accounts:
        _abort(
            f"Unknown account '{account_name}'. "
            f"Valid accounts: {', '.join(sorted(accounts))}"
        )
    acct = accounts[account_name]
    load_dotenv(SCRIPT_DIR / ".env")
    if withdraw:
        key_env = acct.get("withdraw_key_env")
        secret_env = acct.get("withdraw_secret_env")
        if not (key_env and secret_env and os.environ.get(key_env)
                and os.environ.get(secret_env)):
            _abort(
                f"Withdrawals are off for account '{account_name}'. They are "
                f"optional and need a separate withdraw-only API key. See "
                f"'Optional. Withdrawals' in SETUP.md to turn them on."
            )
        acct = dict(acct, key_env=key_env, secret_env=secret_env)
    key = os.environ.get(acct["key_env"])
    secret = os.environ.get(acct["secret_env"])
    if not key or not secret:
        _abort(
            f"Account '{account_name}' needs {acct['key_env']} and "
            f"{acct['secret_env']} set in .env"
        )
    print(f"Account: {account_name} — {acct.get('description', '')}\n")
    return krakenex.API(key=key, secret=secret)


def get_public_api() -> krakenex.API:
    return krakenex.API()


# ---------------------------------------------------------------------------
# Market data helpers
# ---------------------------------------------------------------------------

def fetch_pair_info(api: krakenex.API, pairs: list) -> dict:
    """Return {requested_pair: pair_info_dict}. Kraken may respond under
    normalized keys, so match results back to requests via altname/wsname."""
    resp = api.query_public("AssetPairs", {"pair": ",".join(pairs)})
    _assert_no_error(resp, "AssetPairs")
    results = list(resp["result"].values())
    out = {}
    for req in pairs:
        match = None
        for info in results:
            names = {info.get("altname", ""), info.get("wsname", "").replace("/", "")}
            if req in names:
                match = info
                break
        if match is None and len(pairs) == 1 and len(results) == 1:
            match = results[0]
        if match is None:
            _abort(f"Pair '{req}' not found in AssetPairs response")
        out[req] = match
    return out


def fetch_last_price(api: krakenex.API, pair: str) -> Decimal:
    resp = api.query_public("Ticker", {"pair": pair})
    _assert_no_error(resp, "Ticker")
    ticker = next(iter(resp["result"].values()))
    return Decimal(ticker["c"][0])


# ---------------------------------------------------------------------------
# Read-only commands
# ---------------------------------------------------------------------------

def cmd_balance(args) -> None:
    api = get_api(args.account)
    resp = api.query_private("Balance")
    _assert_no_error(resp, "Balance")
    balances = {k: Decimal(v) for k, v in resp["result"].items()}
    nonzero = {k: v for k, v in balances.items() if v != 0}
    if not nonzero:
        print("No non-zero balances.")
        return
    print(f"{'Asset':<12} {'Balance':>24}")
    print("-" * 38)
    for asset in sorted(nonzero):
        print(f"{asset:<12} {nonzero[asset]:>24,f}")


def cmd_ticker(args) -> None:
    api = get_public_api()
    for pair in args.pairs:
        resp = api.query_public("Ticker", {"pair": pair})
        if resp.get("error"):
            print(f"{pair:<12} ERROR: {resp['error']}")
            continue
        t = next(iter(resp["result"].values()))
        print(
            f"{pair:<12} last={t['c'][0]}  bid={t['b'][0]}  ask={t['a'][0]}  "
            f"24h: low={t['l'][1]} high={t['h'][1]} vol={t['v'][1]}"
        )


def cmd_pair_info(args) -> None:
    api = get_public_api()
    info = fetch_pair_info(api, [args.pair])[args.pair]
    print(f"Pair            : {args.pair} ({info.get('wsname', '')})")
    print(f"Base / Quote    : {info.get('base')} / {info.get('quote')}")
    print(f"Min order size  : {info.get('ordermin')}")
    print(f"Min order cost  : {info.get('costmin', 'n/a')}")
    print(f"Lot decimals    : {info.get('lot_decimals')}  (volume precision)")
    print(f"Pair decimals   : {info.get('pair_decimals')}  (price precision)")
    print(f"Order types     : {', '.join(sorted(ORDERTYPE_PRICES))}")


def _print_order_rows(orders: dict) -> None:
    if not orders:
        print("(none)")
        return
    print(f"{'TxID':<22} {'Pair':<10} {'Type':<5} {'Ordertype':<20} "
          f"{'Price':>14} {'Volume':>18} {'Filled':>18} {'Status':<8}")
    print("-" * 122)
    for txid, o in orders.items():
        d = o.get("descr", {})
        print(
            f"{txid:<22} {d.get('pair', ''):<10} {d.get('type', ''):<5} "
            f"{d.get('ordertype', ''):<20} {d.get('price', ''):>14} "
            f"{o.get('vol', ''):>18} {o.get('vol_exec', ''):>18} "
            f"{o.get('status', 'open'):<8}"
        )


def cmd_open_orders(args) -> None:
    api = get_api(args.account)
    resp = api.query_private("OpenOrders")
    _assert_no_error(resp, "OpenOrders")
    orders = resp["result"].get("open", {})
    print(f"Open orders: {len(orders)}\n")
    _print_order_rows(orders)


def cmd_closed_orders(args) -> None:
    api = get_api(args.account)
    resp = api.query_private("ClosedOrders")
    _assert_no_error(resp, "ClosedOrders")
    closed = resp["result"].get("closed", {})
    items = sorted(closed.items(), key=lambda kv: kv[1].get("closetm", 0), reverse=True)
    items = items[: args.count]
    print(f"Closed orders (most recent {len(items)}):\n")
    _print_order_rows(dict(items))


def cmd_trades(args) -> None:
    api = get_api(args.account)
    resp = api.query_private("TradesHistory")
    _assert_no_error(resp, "TradesHistory")
    trades = resp["result"].get("trades", {})
    items = sorted(trades.items(), key=lambda kv: kv[1].get("time", 0), reverse=True)
    items = items[: args.count]
    print(f"Recent fills (most recent {len(items)}):\n")
    if not items:
        print("(none)")
        return
    print(f"{'Time (UTC)':<20} {'Pair':<10} {'Type':<5} {'Price':>14} "
          f"{'Volume':>18} {'Cost':>14} {'Fee':>10}")
    print("-" * 98)
    for _, t in items:
        ts = datetime.fromtimestamp(t["time"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"{ts:<20} {t.get('pair', ''):<10} {t.get('type', ''):<5} "
            f"{t.get('price', ''):>14} {t.get('vol', ''):>18} "
            f"{t.get('cost', ''):>14} {t.get('fee', ''):>10}"
        )


# ---------------------------------------------------------------------------
# Order-file loading and validation
# ---------------------------------------------------------------------------

def load_order_file(path: str, account: str) -> dict:
    p = Path(path)
    if not p.exists():
        _abort(f"Order file not found: {path}")
    with open(p) as f:
        try:
            doc = json.load(f)
        except json.JSONDecodeError as e:
            _abort(f"Order file is not valid JSON: {e}")

    if doc.get("account") != account:
        _abort(
            f"Account mismatch: order file says account "
            f"'{doc.get('account')}' but --account is '{account}'. "
            f"Both must agree before anything is sent."
        )
    orders = doc.get("orders")
    if not isinstance(orders, list) or not orders:
        _abort("Order file has no 'orders' list")

    for i, order in enumerate(orders, 1):
        label = order.get("label", f"order {i}")
        unknown = set(order) - ALLOWED_ORDER_KEYS
        if unknown:
            _abort(f"{label}: unknown key(s) {sorted(unknown)} — "
                   f"allowed: {sorted(ALLOWED_ORDER_KEYS)}")
        missing = REQUIRED_ORDER_KEYS - set(order)
        if missing:
            _abort(f"{label}: missing required key(s) {sorted(missing)}")
        for k, v in order.items():
            if not isinstance(v, str):
                _abort(f"{label}: value for '{k}' must be a quoted string "
                       f"(got {type(v).__name__}) — numbers as strings, always")
        if order["type"] not in ("buy", "sell"):
            _abort(f"{label}: type must be 'buy' or 'sell'")
        ot = order["ordertype"]
        if ot not in ORDERTYPE_PRICES:
            _abort(f"{label}: unsupported ordertype '{ot}'. "
                   f"Supported: {', '.join(sorted(ORDERTYPE_PRICES))}")
        required, forbidden = ORDERTYPE_PRICES[ot]
        for field in required:
            if field not in order:
                _abort(f"{label}: ordertype '{ot}' requires '{field}'")
        for field in forbidden:
            if field in order:
                _abort(f"{label}: ordertype '{ot}' must not have '{field}'")
    return doc


def _build_api_params(order: dict) -> dict:
    return {k: v for k, v in order.items() if k != "label"}


# ---------------------------------------------------------------------------
# Preflight: local checks before anything is sent to Kraken
# ---------------------------------------------------------------------------

def run_preflight(api: krakenex.API, doc: dict) -> None:
    orders = doc["orders"]
    pairs = sorted({o["pair"] for o in orders})

    print("=" * 60)
    print("PRE-FLIGHT CHECKS")
    print("=" * 60)

    print(f"\n[1/4] Fetching pair specifications for {', '.join(pairs)}...")
    pair_info = fetch_pair_info(api, pairs)
    for pair in pairs:
        info = pair_info[pair]
        print(f"      {pair}: min={info.get('ordermin')}  "
              f"lot_decimals={info.get('lot_decimals')}  "
              f"pair_decimals={info.get('pair_decimals')}")

    print("\n[2/4] Fetching current prices...")
    prices = {}
    for pair in pairs:
        prices[pair] = fetch_last_price(api, pair)
        print(f"      {pair}: last = {prices[pair]}")

    print("\n[3/4] Checking balances...")
    # BalanceEx, not Balance: funds reserved by open orders (hold_trade) are
    # not spendable, and Kraken rejects new orders against them.
    resp = api.query_private("BalanceEx")
    _assert_no_error(resp, "BalanceEx")
    balances = {k: Decimal(v["balance"]) - Decimal(v.get("hold_trade", "0"))
                for k, v in resp["result"].items()}

    sell_needed = {}   # base asset -> volume required
    buy_cost = {}      # quote asset -> estimated cost
    for o in orders:
        info = pair_info[o["pair"]]
        if o["type"] == "sell":
            base = info["base"]
            sell_needed[base] = sell_needed.get(base, Decimal(0)) + Decimal(o["volume"])
        else:
            quote = info["quote"]
            price_str = o.get("price", "")
            price = Decimal(price_str) if price_str and not price_str.startswith(("+", "-")) \
                else prices[o["pair"]]
            buy_cost[quote] = buy_cost.get(quote, Decimal(0)) + price * Decimal(o["volume"])

    for asset, needed in sell_needed.items():
        have = balances.get(asset, Decimal(0))
        if have < needed:
            _abort(f"Insufficient {asset} to sell. "
                   f"Required: {needed:,f}, Available: {have:,f}")
        print(f"      ✓ {asset}: selling {needed:,f} of {have:,f} available")
    for asset, cost in buy_cost.items():
        have = balances.get(asset, Decimal(0))
        flag = "✓" if have >= cost else "⚠"
        print(f"      {flag} {asset}: buys cost ≈ {cost:,.2f}, available {have:,.2f}"
              + ("" if have >= cost else "  (WARNING: may be insufficient)"))

    print("\n[4/4] Validating order precision and minimums...")
    for order in orders:
        label = order.get("label", order["pair"])
        info = pair_info[order["pair"]]
        order_min = Decimal(str(info.get("ordermin", "0")))
        lot_decimals = int(info.get("lot_decimals", 8))
        pair_decimals = int(info.get("pair_decimals", 8))

        vol = Decimal(order["volume"])
        if vol < order_min:
            _abort(f"{label}: volume {vol} is below minimum {order_min}")
        if "." in order["volume"]:
            used = len(order["volume"].split(".")[1])
            if used > lot_decimals:
                _abort(f"{label}: volume has {used} decimal places "
                       f"but pair allows {lot_decimals}")
        dv = order.get("displayvol")
        if dv is not None:
            dvol = Decimal(dv)
            if dvol < order_min:
                _abort(f"{label}: displayvol {dvol} is below minimum {order_min}")
            if dvol > vol:
                _abort(f"{label}: displayvol {dvol} exceeds volume {vol}")
        # Percentage strings (trailing-stop offsets like "+18.0%") skip
        # numeric precision checks — Kraken validates those server-side.
        for price_field in ("price", "price2"):
            p = order.get(price_field, "")
            if p and not p.startswith(("+", "-")):
                try:
                    p_str = format(Decimal(p), "f")
                    if "." in p_str:
                        used = len(p_str.split(".")[1].rstrip("0") or "")
                        if used > pair_decimals:
                            _abort(f"{label}: {price_field}={p} has {used} decimal "
                                   f"places but pair allows {pair_decimals}")
                except InvalidOperation:
                    _abort(f"{label}: {price_field}='{p}' is not a valid number")
        print(f"      ✓ {label}")

    print("\nPre-flight PASSED.\n")


# ---------------------------------------------------------------------------
# place — dry-run and live
# ---------------------------------------------------------------------------

def show_orders(doc: dict, heading: str) -> None:
    print("=" * 60)
    print(heading)
    print("=" * 60)
    for i, order in enumerate(doc["orders"], 1):
        print(f"\n  [{i}] {order.get('label', '(no label)')}")
        for k, v in _build_api_params(order).items():
            print(f"        {k:<14} = {v}")
    print()


def run_validate(api: krakenex.API, doc: dict) -> None:
    """Server-side dry-run: AddOrder with validate=true places nothing."""
    print("=" * 60)
    print("SERVER VALIDATION — validate=true (no orders placed)")
    print("=" * 60)
    failures = 0
    for i, order in enumerate(doc["orders"], 1):
        label = order.get("label", f"order {i}")
        params = _build_api_params(order)
        params["validate"] = "true"
        resp = api.query_private("AddOrder", params)
        if resp.get("error"):
            print(f"  [{i}/{len(doc['orders'])}] FAILED  {label}")
            print(f"        {resp['error']}")
            failures += 1
        else:
            descr = resp["result"].get("descr", {}).get("order", "")
            print(f"  [{i}/{len(doc['orders'])}] OK      {label}")
            if descr:
                print(f"        Kraken reads this as: {descr}")
    print()
    if failures:
        _abort(f"{failures} order(s) failed server validation — nothing was placed")
    print("All orders passed server validation.\n")


def write_log(entry: dict) -> None:
    LOG_FILE.parent.mkdir(exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def place_live(api: krakenex.API, doc: dict, account: str, source_file: str) -> None:
    orders = doc["orders"]
    print("!" * 60)
    print(f"  WARNING: This will place {len(orders)} REAL order(s) on the")
    print(f"  '{account}' Kraken account.")
    print("!" * 60)
    answer = input(f"\nType CONFIRM to place all {len(orders)} orders, "
                   f"or anything else to abort: ")
    if answer.strip() != "CONFIRM":
        print("Aborted.")
        sys.exit(0)
    print()

    print("=" * 60)
    print("PLACING ORDERS")
    print("=" * 60)
    results = []
    for i, order in enumerate(orders, 1):
        label = order.get("label", f"order {i}")
        print(f"\n[{i}/{len(orders)}] {label}")
        params = _build_api_params(order)
        resp = api.query_private("AddOrder", params)
        ts = _utc_now()
        error = resp.get("error") or None
        txids = resp.get("result", {}).get("txid", []) if not error else []
        txid = ", ".join(txids) if txids else None
        descr = resp.get("result", {}).get("descr", {}).get("order", "") if not error else ""
        write_log({
            "ts": ts, "event": "add_order", "account": account,
            "source_file": source_file, "label": label, "params": params,
            "txid": txid, "descr": descr,
            "error": error if not error else [str(e) for e in error],
        })
        if error:
            print(f"  FAILED: {error}")
            _print_summary(results, orders, failed_at=label)
            sys.exit(1)
        print(f"  Order ID  : {txid or '(no txid returned)'}")
        print(f"  Status    : open")
        print(f"  Timestamp : {ts}")
        results.append({"label": label, "txid": txid, "order": order})

    _print_summary(results, orders)


def _print_summary(results: list, orders: list, failed_at=None) -> None:
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    totals = {}
    for r in results:
        print(f"  OK       {r['label']}")
        print(f"           ID: {r['txid']}")
        o = r["order"]
        key = (o["pair"], o["type"])
        totals[key] = totals.get(key, Decimal(0)) + Decimal(o["volume"])
    if failed_at:
        print(f"\n  STOPPED at: {failed_at}")
    for (pair, side), vol in totals.items():
        print(f"\n  Total {side} volume placed on {pair}: {vol:,f}")
    print("=" * 60)


def cmd_place(args) -> None:
    api = get_api(args.account)
    doc = load_order_file(args.file, args.account)
    if doc.get("description"):
        print(f"Order file : {args.file}")
        print(f"Description: {doc['description']}\n")

    run_preflight(api, doc)

    if not args.live:
        show_orders(doc, "DRY-RUN — Orders that WOULD be sent to Kraken")
        run_validate(api, doc)
        print("Dry-run complete. Nothing was placed.")
        print(f"To place for real, run:\n"
              f"  python3 kraken.py place {args.file} --account {args.account} --live")
        return

    show_orders(doc, "LIVE MODE — Orders to be placed on Kraken")
    place_live(api, doc, args.account, args.file)


# ---------------------------------------------------------------------------
# split — generate order files (never touches the private API)
# ---------------------------------------------------------------------------

def _quantize_down(value: Decimal, decimals: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN)


def _quantize_price(value: Decimal, decimals: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_EVEN)


def allocate_volumes(total: Decimal, weights: list, lot_decimals: int) -> list:
    """Split total into len(weights) shares proportional to weights.
    Exact: sum(shares) == total, every share on the lot grid.
    Remainder goes to the largest fractional parts first (largest-remainder)."""
    quantum = Decimal(1).scaleb(-lot_decimals)
    if total % quantum != 0:
        _abort(f"Total volume {total} is not a multiple of the lot size {quantum}")
    wsum = sum(weights)
    raw = [total * w / wsum for w in weights]
    shares = [r.quantize(quantum, rounding=ROUND_DOWN) for r in raw]
    remainder = total - sum(shares)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - shares[i], reverse=True)
    i = 0
    while remainder > 0:
        shares[order[i % len(shares)]] += quantum
        remainder -= quantum
        i += 1
    assert sum(shares) == total, "allocation invariant violated"
    return shares


def _parse_offset_pct(offset: str) -> Decimal:
    """'-3%' -> Decimal('0.97'); '+2.5%' -> Decimal('1.025')"""
    s = offset.strip().rstrip("%")
    try:
        return Decimal(1) + Decimal(s) / 100
    except InvalidOperation:
        _abort(f"Bad --limit-offset '{offset}' (expected e.g. -3%)")


def _write_order_doc(args, orders: list, description: str) -> None:
    doc = {
        "version": 1,
        "account": args.account,
        "description": description,
        "generated_by": "kraken.py " + " ".join(sys.argv[1:]),
        "created": _utc_now(),
        "orders": orders,
    }
    out = Path(args.output)
    out.parent.mkdir(exist_ok=True)
    if out.exists() and not args.force:
        _abort(f"{out} already exists — pass --force to overwrite")
    with open(out, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(f"\nWrote {out}")
    print(f"Review it, then dry-run:\n"
          f"  python3 kraken.py place {out} --account {args.account}")


def _print_split_table(rows: list, total: Decimal) -> None:
    print(f"\n{'#':<3} {'Price':>16} {'Volume':>20} {'% of total':>10}   {'Running sum':>20}")
    print("-" * 76)
    running = Decimal(0)
    for i, (price, vol) in enumerate(rows, 1):
        running += vol
        pct = (vol / total * 100).quantize(Decimal("0.01"))
        print(f"{i:<3} {price:>16} {format(vol, 'f'):>20} {pct:>9}%   {format(running, 'f'):>20}")
    match = "✓ exact" if running == total else "✗ MISMATCH"
    print(f"\nTotal: {format(running, 'f')} / {format(total, 'f')}  {match}")


def cmd_split_ladder(args) -> None:
    api = get_public_api()
    info = fetch_pair_info(api, [args.pair])[args.pair]
    lot_decimals = int(info["lot_decimals"])
    pair_decimals = int(info["pair_decimals"])
    order_min = Decimal(str(info.get("ordermin", "0")))

    if args.prices:
        prices = [Decimal(p) for p in args.prices.split(",")]
        levels = len(prices)
    else:
        if not (args.price_start and args.price_end and args.levels):
            _abort("Provide either --prices, or --price-start/--price-end/--levels")
        levels = args.levels
        start, end = Decimal(args.price_start), Decimal(args.price_end)
        if levels < 2:
            _abort("--levels must be at least 2 (use split iceberg/chunk for one order)")
        step = (end - start) / (levels - 1)
        prices = [_quantize_price(start + step * i, pair_decimals) for i in range(levels)]

    for p in prices:
        p_str = format(p, "f")
        if "." in p_str and len(p_str.split(".")[1]) > pair_decimals:
            _abort(f"Price {p} exceeds {pair_decimals} decimal places for {args.pair}")

    if args.weights:
        weights = [Decimal(w) for w in args.weights.split(",")]
        if len(weights) != levels:
            _abort(f"--weights has {len(weights)} entries but there are {levels} levels")
    else:
        weights = [Decimal(1)] * levels

    total = Decimal(args.total_volume)
    shares = allocate_volumes(total, weights, lot_decimals)
    for i, share in enumerate(shares):
        if share < order_min:
            _abort(f"Level {i + 1} volume {share} is below pair minimum {order_min} — "
                   f"use fewer levels or adjust weights")

    factor = _parse_offset_pct(args.limit_offset) if args.limit_offset else None
    orders = []
    for i, (price, vol) in enumerate(zip(prices, shares), 1):
        pct = (Decimal(weights[i - 1]) / sum(weights) * 100).quantize(Decimal("0.1"))
        order = {
            "label": f"L{i} — {args.ordertype} {args.side} {pct}% @ {format(price, 'f')}",
            "pair": args.pair,
            "type": args.side,
            "ordertype": args.ordertype,
            "price": format(price, "f"),
            "volume": format(vol, "f"),
        }
        if factor is not None:
            order["price2"] = format(_quantize_price(price * factor, pair_decimals), "f")
        if args.trigger:
            order["trigger"] = args.trigger
        if args.oflags:
            order["oflags"] = args.oflags
        order["timeinforce"] = args.tif
        required, forbidden = ORDERTYPE_PRICES.get(args.ordertype, (set(), set()))
        if "price2" in required and "price2" not in order:
            _abort(f"ordertype '{args.ordertype}' needs --limit-offset to derive price2")
        if "price2" in forbidden:
            order.pop("price2", None)
        orders.append(order)

    _print_split_table(list(zip([format(p, "f") for p in prices], shares)), total)
    desc = (f"{args.pair} {args.side} ladder — {levels} levels, "
            f"total {format(total, 'f')}")
    _write_order_doc(args, orders, desc)


def cmd_split_chunk(args) -> None:
    api = get_public_api()
    info = fetch_pair_info(api, [args.pair])[args.pair]
    lot_decimals = int(info["lot_decimals"])
    order_min = Decimal(str(info.get("ordermin", "0")))

    total = Decimal(args.total_volume)
    shares = allocate_volumes(total, [Decimal(1)] * args.chunks, lot_decimals)
    for i, share in enumerate(shares):
        if share < order_min:
            _abort(f"Chunk {i + 1} volume {share} is below pair minimum {order_min}")

    orders = []
    for i, vol in enumerate(shares, 1):
        order = {
            "label": f"C{i}/{args.chunks} — limit {args.side} @ {args.price}",
            "pair": args.pair,
            "type": args.side,
            "ordertype": "limit",
            "price": args.price,
            "volume": format(vol, "f"),
            "timeinforce": args.tif,
        }
        if args.oflags:
            order["oflags"] = args.oflags
        orders.append(order)

    _print_split_table([(args.price, v) for v in shares], total)
    desc = f"{args.pair} {args.side} split into {args.chunks} chunks @ {args.price}"
    _write_order_doc(args, orders, desc)


def cmd_split_iceberg(args) -> None:
    api = get_public_api()
    info = fetch_pair_info(api, [args.pair])[args.pair]
    order_min = Decimal(str(info.get("ordermin", "0")))

    vol, dvol = Decimal(args.total_volume), Decimal(args.display)
    if dvol < order_min:
        _abort(f"Display volume {dvol} is below pair minimum {order_min}")
    if dvol > vol:
        _abort(f"Display volume {dvol} exceeds total volume {vol}")
    if dvol < vol / 25:
        _abort(f"Kraken requires display volume ≥ 1/25 of total "
               f"({format(_quantize_down(vol / 25, int(info['lot_decimals'])), 'f')})")

    order = {
        "label": f"Iceberg — limit {args.side} {args.total_volume} @ {args.price} "
                 f"(display {args.display})",
        "pair": args.pair,
        "type": args.side,
        "ordertype": "limit",
        "price": args.price,
        "volume": args.total_volume,
        "displayvol": args.display,
        "timeinforce": args.tif,
    }
    if args.oflags:
        order["oflags"] = args.oflags

    _print_split_table([(args.price, vol)], vol)
    desc = f"{args.pair} {args.side} iceberg @ {args.price}, display {args.display}"
    _write_order_doc(args, [order], desc)


# ---------------------------------------------------------------------------
# Withdrawals (optional, off by default). They use a separate withdraw-only
# API key so the everyday trading key never has withdrawal permission.
# Funds can only go to addresses already saved (and email-verified) in the
# Kraken web UI. The API cannot add addresses; it refers to them by the name
# ("key") you gave them there. Dry-run unless --live.
# ---------------------------------------------------------------------------

def _withdraw_error_hint(resp: dict, endpoint: str) -> None:
    errors = resp.get("error") or []
    if any("Permission denied" in str(e) for e in errors):
        _abort(f"Kraken {endpoint}: permission denied. The withdraw key needs "
               f"the 'Query Funds' and 'Withdraw Funds' permissions")
    _assert_no_error(resp, endpoint)


def resolve_asset(asset: str) -> tuple:
    """Return (altname, balance_code) for a user-supplied asset, e.g.
    BTC -> ('XBT', 'XXBT'), SOL -> ('SOL', 'SOL'). Kraken answers 'BTC' under
    a non-balance key, so re-query by altname to get the canonical code."""
    api = get_public_api()
    resp = api.query_public("Assets", {"asset": asset.upper()})
    if resp.get("error"):
        _abort(f"Unknown asset '{asset}': {resp['error']}")
    altname = next(iter(resp["result"].values()))["altname"]
    resp = api.query_public("Assets", {"asset": altname})
    _assert_no_error(resp, "Assets")
    return altname, next(iter(resp["result"]))


def asset_display_names() -> dict:
    """Map Kraken's internal asset codes to the names people type,
    e.g. XXRP -> XRP, XXLM -> XLM, ZUSD -> USD."""
    resp = get_public_api().query_public("Assets")
    if resp.get("error"):
        return {}
    return {code: info.get("altname", code) for code, info in resp["result"].items()}


def _fmt_aligned(value: str, int_width: int = 15, frac_width: int = 10) -> str:
    """Right-align the whole part and left-align the fraction so the decimal
    points line up no matter how many decimals each asset uses."""
    whole, _, frac = f"{Decimal(value):,f}".partition(".")
    return f"{whole:>{int_width}}" + (f".{frac:<{frac_width}}" if frac else " " * (frac_width + 1))


def fetch_withdraw_addresses(api: krakenex.API, asset: str = None) -> list:
    params = {"asset": asset} if asset else {}
    resp = api.query_private("WithdrawAddresses", params)
    _withdraw_error_hint(resp, "WithdrawAddresses")
    return resp["result"]


def cmd_withdraw_addresses(args) -> None:
    api = get_api(args.account, withdraw=True)
    asset = resolve_asset(args.asset)[0] if args.asset else None
    rows = fetch_withdraw_addresses(api, asset)
    if not rows:
        print("No saved withdrawal addresses"
              + (f" for {asset}." if asset else ".")
              + "\nAdd them in the Kraken web UI: Funding > Withdraw > Add address.")
        return
    print(f"{'Name (use with --to)':<28} {'Asset':<6} {'Verified':<9} {'Method':<22} Address")
    print("-" * 110)
    for r in sorted(rows, key=lambda r: (r.get("asset", ""), r.get("key", ""))):
        addr = r.get("address", "")
        if r.get("memo"):
            addr += f"  memo={r['memo']}"
        print(f"{r.get('key', ''):<28} {r.get('asset', ''):<6} "
              f"{'yes' if r.get('verified') else 'NO':<9} "
              f"{r.get('method', ''):<22} {addr}")


def cmd_withdraw(args) -> None:
    try:
        amount = Decimal(args.amount)
    except InvalidOperation:
        _abort(f"Amount '{args.amount}' is not a number")
    if amount <= 0:
        _abort("Amount must be positive")

    api = get_api(args.account, withdraw=True)
    asset, balance_code = resolve_asset(args.asset)

    matches = [r for r in fetch_withdraw_addresses(api, asset)
               if r.get("key") == args.to]
    if not matches:
        _abort(f"No saved {asset} withdrawal address named '{args.to}' on "
               f"'{args.account}'. List them with:\n"
               f"  python3 kraken.py withdraw-addresses --account {args.account} "
               f"--asset {asset}")
    if len(matches) > 1:
        _abort(f"'{args.to}' matches {len(matches)} saved addresses. Rename them "
               f"in Kraken so the name is unique")
    dest = matches[0]
    if not dest.get("verified"):
        _abort(f"Address '{args.to}' is not verified yet. Confirm it via the "
               f"Kraken email before withdrawing")

    resp = api.query_private("BalanceEx")
    _assert_no_error(resp, "BalanceEx")
    bal = resp["result"].get(balance_code) or resp["result"].get(asset)
    available = (Decimal(bal["balance"]) - Decimal(bal.get("hold_trade", "0"))
                 if bal else Decimal(0))
    if amount > available:
        _abort(f"Amount {amount:,f} {asset} exceeds available balance "
               f"{available:,f} (balance minus funds held in open orders)")

    # Kraken answers a below-minimum WithdrawInfo with a bare "Invalid
    # arguments", so check the per-network minimum ourselves first.
    resp = api.query_private("WithdrawMethods", {"asset": asset})
    _withdraw_error_hint(resp, "WithdrawMethods")
    methods = [m for m in resp["result"] if m.get("method") == dest.get("method")]
    minimum = Decimal(methods[0]["minimum"]) if methods and methods[0].get("minimum") else None
    if minimum is not None and amount < minimum:
        _abort(f"Amount {amount:,f} {asset} is below Kraken's minimum withdrawal "
               f"of {minimum:,f} {asset} on {dest.get('method')}")

    resp = api.query_private("WithdrawInfo",
                             {"asset": asset, "key": args.to, "amount": args.amount})
    _withdraw_error_hint(resp, "WithdrawInfo")
    info = resp["result"]
    fee = Decimal(info["fee"])
    if args.max_fee is not None and fee > Decimal(args.max_fee):
        _abort(f"Quoted fee {fee:,f} {asset} exceeds --max-fee {args.max_fee}")

    print("=" * 60)
    print(("LIVE MODE" if args.live else "DRY-RUN") + " — Withdrawal")
    print("=" * 60)
    print(f"  Asset        : {asset}")
    print(f"  Amount       : {amount:,f}  (fee comes out of this)")
    print(f"  Kraken fee   : {fee:,f}")
    print(f"  Arrives      : {Decimal(info['amount']):,f}")
    print(f"  Limit        : {Decimal(info['limit']):,f}  (max withdrawable now)")
    print(f"  To (name)    : {args.to}")
    print(f"  To (address) : {dest['address']}")
    if dest.get("memo"):
        print(f"  Memo / tag   : {dest['memo']}")
    print(f"  Network      : {dest.get('method') or info.get('method', '')}")
    print(f"  Available    : {available:,f} {asset}")
    print()

    if not args.live:
        print("Dry-run complete. Nothing was withdrawn.")
        print(f"To withdraw for real, run:\n"
              f"  python3 kraken.py withdraw {asset} {args.amount} --to \"{args.to}\" "
              f"--account {args.account} --live")
        return

    tail = dest["address"][-6:]
    print("!" * 60)
    print(f"  WARNING: This sends REAL funds off the '{args.account}' account.")
    print("  Crypto withdrawals cannot be reversed.")
    print("!" * 60)
    answer = input(f"\nType the LAST 6 characters of the destination address "
                   f"to withdraw, or anything else to abort: ")
    if answer.strip() != tail:
        print("Aborted.")
        sys.exit(0)

    # address + max_fee make Kraken itself reject the request if the saved
    # address changed or the fee rose since the quote shown above.
    params = {"asset": asset, "key": args.to, "amount": args.amount,
              "address": dest["address"], "max_fee": info["fee"]}
    resp = api.query_private("Withdraw", params)
    error = resp.get("error") or None
    refid = resp.get("result", {}).get("refid") if not error else None
    write_log({
        "ts": _utc_now(), "event": "withdraw", "account": args.account,
        "params": params, "refid": refid,
        "error": error if not error else [str(e) for e in error],
    })
    if error:
        _abort(f"Withdraw failed: {error}")
    print(f"\nWithdrawal submitted. Reference ID: {refid}")
    print(f"Track it with: python3 kraken.py withdraw-status --account "
          f"{args.account} --asset {asset}")


def cmd_withdraw_status(args) -> None:
    api = get_api(args.account, withdraw=True)
    params = {"asset": resolve_asset(args.asset)[0]} if args.asset else {}
    resp = api.query_private("WithdrawStatus", params)
    _withdraw_error_hint(resp, "WithdrawStatus")
    rows = resp["result"]
    if not rows:
        print("No recent withdrawals.")
        return
    names = asset_display_names()
    print(f"{'Time (UTC)':<20} {'Asset':<6} {'Amount':>26} {'Fee':>22}  "
          f"{'Status':<10} {'RefID':<31} TxID")
    print("-" * 150)
    for r in sorted(rows, key=lambda r: r.get("time", 0), reverse=True):
        ts = datetime.fromtimestamp(r.get("time", 0), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        asset = names.get(r.get("asset", ""), r.get("asset", ""))
        print(f"{ts:<20} {asset:<6} {_fmt_aligned(r.get('amount', '0'))} "
              f"{_fmt_aligned(r.get('fee', '0'), 11)}  {r.get('status', ''):<10} "
              f"{r.get('refid', ''):<31} {r.get('txid') or ''}")


# ---------------------------------------------------------------------------
# cancel / cancel-all
# ---------------------------------------------------------------------------

def cmd_cancel(args) -> None:
    api = get_api(args.account)
    resp = api.query_private("QueryOrders", {"txid": ",".join(args.txids)})
    _assert_no_error(resp, "QueryOrders")
    found = resp["result"]
    print("Orders to cancel:\n")
    _print_order_rows(found)
    missing = [t for t in args.txids if t not in found]
    if missing:
        _abort(f"TxID(s) not found on this account: {', '.join(missing)}")
    answer = input(f"\nCancel {len(args.txids)} order(s)? [y/N] ")
    if answer.strip().lower() != "y":
        print("Aborted.")
        return
    for txid in args.txids:
        resp = api.query_private("CancelOrder", {"txid": txid})
        error = resp.get("error") or None
        write_log({
            "ts": _utc_now(), "event": "cancel_order", "account": args.account,
            "txid": txid,
            "error": error if not error else [str(e) for e in error],
        })
        if error:
            print(f"  FAILED  {txid}: {error}")
        else:
            print(f"  OK      {txid} cancelled")


def cmd_cancel_all(args) -> None:
    api = get_api(args.account)
    resp = api.query_private("OpenOrders")
    _assert_no_error(resp, "OpenOrders")
    orders = resp["result"].get("open", {})
    if not orders:
        print("No open orders to cancel.")
        return
    print(f"This will cancel ALL {len(orders)} open order(s) on "
          f"'{args.account}':\n")
    _print_order_rows(orders)
    answer = input("\nType CONFIRM to cancel all of these orders: ")
    if answer.strip() != "CONFIRM":
        print("Aborted.")
        return
    resp = api.query_private("CancelAll")
    error = resp.get("error") or None
    write_log({
        "ts": _utc_now(), "event": "cancel_all", "account": args.account,
        "count": resp.get("result", {}).get("count"),
        "error": error if not error else [str(e) for e in error],
    })
    _assert_no_error(resp, "CancelAll")
    print(f"Cancelled {resp['result'].get('count', '?')} order(s).")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _add_account_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--account", required=True,
                   help="Account name from accounts.json (e.g. main). Required.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kraken.py",
        description="Kraken trading CLI — dry-run by default, --live requires CONFIRM.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("balance", help="Show non-zero balances")
    _add_account_arg(p)
    p.set_defaults(func=cmd_balance)

    p = sub.add_parser("ticker", help="Show current prices (public)")
    p.add_argument("pairs", nargs="+", metavar="PAIR")
    p.set_defaults(func=cmd_ticker)

    p = sub.add_parser("pair-info", help="Show pair minimums and precision (public)")
    p.add_argument("pair", metavar="PAIR")
    p.set_defaults(func=cmd_pair_info)

    p = sub.add_parser("open-orders", help="List open orders")
    _add_account_arg(p)
    p.set_defaults(func=cmd_open_orders)

    p = sub.add_parser("closed-orders", help="List recent closed orders")
    _add_account_arg(p)
    p.add_argument("--count", type=int, default=20)
    p.set_defaults(func=cmd_closed_orders)

    p = sub.add_parser("trades", help="List recent fills")
    _add_account_arg(p)
    p.add_argument("--count", type=int, default=20)
    p.set_defaults(func=cmd_trades)

    p = sub.add_parser("place", help="Place orders from a JSON order file "
                                     "(dry-run unless --live)")
    p.add_argument("file", help="Path to order file (see orders/)")
    _add_account_arg(p)
    p.add_argument("--live", action="store_true",
                   help="Place real orders (requires typing CONFIRM)")
    p.set_defaults(func=cmd_place)

    split = sub.add_parser("split", help="Generate an order file (places nothing)")
    split_sub = split.add_subparsers(dest="split_kind", required=True)

    def _common_split_args(sp, with_price=False):
        sp.add_argument("--pair", required=True)
        sp.add_argument("--side", required=True, choices=["buy", "sell"])
        sp.add_argument("--total-volume", required=True)
        if with_price:
            sp.add_argument("--price", required=True)
        sp.add_argument("--oflags", default=None,
                        help="e.g. 'post' for post-only maker orders")
        sp.add_argument("--tif", default="GTC", help="timeinforce (default GTC)")
        _add_account_arg(sp)
        sp.add_argument("-o", "--output", required=True,
                        help="Output order file, e.g. orders/2026-09-07_main_SOLUSD_ladder.json")
        sp.add_argument("--force", action="store_true",
                        help="Overwrite the output file if it exists")

    sp = split_sub.add_parser("ladder", help="Split volume across N price levels")
    _common_split_args(sp)
    sp.add_argument("--ordertype", default="limit",
                    choices=sorted(set(ORDERTYPE_PRICES) - {"market"}))
    sp.add_argument("--levels", type=int)
    sp.add_argument("--price-start")
    sp.add_argument("--price-end")
    sp.add_argument("--prices", help="Explicit comma-separated price list "
                                     "(overrides start/end/levels)")
    sp.add_argument("--weights", help="Comma-separated weights, default equal")
    sp.add_argument("--limit-offset",
                    help="Derive price2 from price; use the = form for negative "
                         "values, e.g. --limit-offset=-3%% (for *-limit trigger types)")
    sp.add_argument("--trigger", default=None, choices=["last", "index"])
    sp.set_defaults(func=cmd_split_ladder)

    sp = split_sub.add_parser("chunk", help="Split volume into N equal limit orders")
    _common_split_args(sp, with_price=True)
    sp.add_argument("--chunks", type=int, required=True)
    sp.set_defaults(func=cmd_split_chunk)

    sp = split_sub.add_parser("iceberg", help="Single limit order showing only "
                                              "part of its volume")
    _common_split_args(sp, with_price=True)
    sp.add_argument("--display", required=True,
                    help="Visible volume (>= 1/25 of total)")
    sp.set_defaults(func=cmd_split_iceberg)

    p = sub.add_parser("cancel", help="Cancel order(s) by TxID")
    p.add_argument("txids", nargs="+", metavar="TXID")
    _add_account_arg(p)
    p.set_defaults(func=cmd_cancel)

    p = sub.add_parser("cancel-all", help="Cancel ALL open orders on an account")
    _add_account_arg(p)
    p.set_defaults(func=cmd_cancel_all)

    p = sub.add_parser("withdraw-addresses",
                       help="List withdrawal addresses saved in Kraken (by name)")
    _add_account_arg(p)
    p.add_argument("--asset", help="Filter by asset, e.g. SOL, BTC")
    p.set_defaults(func=cmd_withdraw_addresses)

    p = sub.add_parser("withdraw", help="Withdraw to a saved address "
                                        "(dry-run unless --live)")
    p.add_argument("asset", metavar="ASSET", help="e.g. SOL, BTC, USDC")
    p.add_argument("amount", metavar="AMOUNT",
                   help="Amount to withdraw, fee included (as a string, e.g. 1.5)")
    p.add_argument("--to", required=True,
                   help="Saved address name exactly as shown by withdraw-addresses")
    _add_account_arg(p)
    p.add_argument("--max-fee", help="Abort if Kraken's quoted fee is above this")
    p.add_argument("--live", action="store_true",
                   help="Send for real (requires typing the address's last 6 chars)")
    p.set_defaults(func=cmd_withdraw)

    p = sub.add_parser("withdraw-status", help="Recent withdrawals and their status")
    _add_account_arg(p)
    p.add_argument("--asset", help="Filter by asset")
    p.set_defaults(func=cmd_withdraw_status)

    args = parser.parse_args()
    try:
        args.func(args)
    except (ConnectionError, TimeoutError, OSError) as e:
        _abort(f"Network error talking to Kraken: {e}")


if __name__ == "__main__":
    main()
