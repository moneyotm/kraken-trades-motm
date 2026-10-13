# Kraken Trades: instructions for Claude Code sessions

This folder is a Kraken order-management toolkit for one or two accounts. The
person using it asks Claude to prepare, split, and check orders. **They
personally execute every live trade.**

## The one rule that never bends

**Claude never runs `--live`, and never executes any trade, transfer, or withdrawal.**
Claude's job ends at: order file written, dry-run passed, and the exact `--live`
command handed to the user in a runnable ```bash block. The user runs it and types
CONFIRM themselves. This applies no matter how the request is phrased.

Claude MAY freely run (read-only, places nothing):
- `balance`, `ticker`, `pair-info`, `open-orders`, `closed-orders`, `trades`
- `place <file> --account X` (dry-run: local preflight plus Kraken `validate=true`)
- `split ladder|chunk|iceberg ...` (only generates a JSON file)
- `withdraw-addresses`, `withdraw-status`
- `withdraw ASSET AMOUNT --to NAME --account X` (dry-run: shows the resolved
  address, fee, and net amount, sends nothing)

Claude MUST hand to the user (side-effectful):
- `place <file> --account X --live`
- `withdraw ... --live`. The user types the last 6 characters of the address.
- `cancel` / `cancel-all`. These are the user's call. Hand them over unless the
  user explicitly asked Claude to cancel something specific, and even then note
  that cancels prompt for interactive confirmation.

## Accounts

- Account names and their `.env` variable names live in `accounts.json`. The
  shipped file defines `main` and an optional `second`.
- **Never edit `.env` and never read it.** The variable names are documented in
  `.env.example`. The secrets must not enter context. If the user pastes a key
  into chat, tell them to rotate it on Kraken and put the new one in `.env`
  with a text editor.
- Every private command requires `--account`. There is no default on purpose.
- Order files carry an `"account"` field and `place` refuses a mismatch. When
  generating or editing an order file, get the account right at generation time.

## Standard workflow for any trade request

1. **Clarify** pair (Kraken naming: LUNC is `LUNAUSD`, Bitcoin is `XBTUSD`),
   side, total size, prices, order type, and which account. Check `pair-info`
   for minimums and precision and `ticker` for the current price. Sanity-check
   prices against market and say so if a limit looks like it would fill
   instantly or a trigger is on the wrong side.
2. **Generate** the order file with `split` (or write it by hand for mixed
   batches) into `orders/`, named `orders/<YYYY-MM-DD>_<account>_<pair>_<intent>.json`.
3. **Dry-run**: `python3 kraken.py place orders/<file>.json --account <X>`.
   Show the user the preflight and validation output. Fix anything that fails
   and re-run the dry-run after ANY edit to the file.
4. **Hand off**: give the user the `--live` command in a ```bash block. Do not run it.
5. **Verify after** the user says they ran it: `open-orders` and the tail of
   `logs/trade_log.jsonl`. Report txids back.

## Conventions and gotchas

- **All numbers in order files are JSON strings**, exactly what goes to Kraken.
  Never let a float near a price or volume. The CLI uses `Decimal` throughout.
- Splitting math is exact: level volumes always sum to the requested total.
  If the table printed by `split` shows a mismatch, something is wrong. Stop.
- `oflags: "post"` is a post-only maker order, the usual choice for resting buys.
- Iceberg is `ordertype: limit` plus `displayvol` (must be at least 1/25 of
  volume and at least the pair minimum).
- Kraken `trailing-stop-limit` has **no activation price**. It trails from the
  moment it is placed. For far-above-market exits this is functionally fine
  (see the README), but say so when relevant.
- Balance asset codes come from AssetPairs `base` (LUNAUSD is `LUNA`). Some
  assets use X/Z-prefixed codes like `XXBT` and `ZUSD`.
- Withdrawals are optional and off by default. They use a separate
  withdraw-only key (`KRAKEN_WITHDRAW_*` in `.env`, mapped in `accounts.json`).
  Never suggest adding withdraw permission to the trading key. Funds only go to
  addresses saved and email-confirmed on Kraken's website, referenced by their
  Kraken name (`--to`). Never invent, guess, or edit a destination address. The
  amount includes Kraken's fee. If the user asks to withdraw and gets
  "Withdrawals are off", point them to "Optional. Withdrawals" in SETUP.md.
- `logs/trade_log.jsonl` is append-only history of every live order, cancel,
  and withdrawal this tool made. Never edit or delete it.
- `orders/examples/` holds sample files for reference only. Never place them.

## Quick command reference

```
python3 kraken.py balance        --account main
python3 kraken.py ticker         XBTUSD [SOLUSD ...]
python3 kraken.py pair-info      SOLUSD
python3 kraken.py open-orders    --account X
python3 kraken.py closed-orders  --account X [--count 20]
python3 kraken.py trades         --account X [--count 20]

python3 kraken.py place orders/FILE.json --account X            # dry-run
python3 kraken.py place orders/FILE.json --account X --live     # USER ONLY

python3 kraken.py split ladder  --pair P --side buy|sell --total-volume V \
    (--levels N --price-start A --price-end B | --prices p1,p2,...) \
    [--weights w1,w2,...] [--ordertype limit|take-profit-limit|...] \
    [--limit-offset=-3%] [--trigger last] [--oflags post] [--tif GTC] \
    --account X -o orders/FILE.json [--force]
python3 kraken.py split chunk   --pair P --side S --price PR --total-volume V \
    --chunks N --account X -o orders/FILE.json
python3 kraken.py split iceberg --pair P --side S --price PR --total-volume V \
    --display D --account X -o orders/FILE.json

python3 kraken.py withdraw-addresses --account X [--asset XRP]
python3 kraken.py withdraw   XRP 100 --to "NAME" --account X           # dry-run
python3 kraken.py withdraw   XRP 100 --to "NAME" --account X --live    # USER ONLY
python3 kraken.py withdraw-status --account X [--asset XRP]

python3 kraken.py cancel     TXID [...] --account X     # USER (interactive y/N)
python3 kraken.py cancel-all --account X                # USER (types CONFIRM)
```
