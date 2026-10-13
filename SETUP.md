# Setting up Kraken Trades with Claude Code

This guide is for Money on the Move members who already have the Claude
desktop app with the Code tab open. You will end up with a folder on your
computer where Claude prepares Kraken orders for you and you press the button
yourself. Plan on about fifteen minutes.

## What you need before you start

- A Kraken account that is verified and funded. The Crypto Setup course on
  Patreon covers opening one.
- The Claude desktop app, signed in to a plan that includes Claude Code (Pro,
  Max, Team, or Enterprise), with the Code tab open.
- A Mac or a Windows PC.

## Step 1. Put this folder on your computer

On the GitHub page for this project, click the green **Code** button, then
**Download ZIP**. Unzip it and move the folder somewhere sensible, such as your
Documents folder. Rename it to `kraken-trades-motm` if it has a longer name.

## Step 2. Open the folder in Claude Code

In the Claude desktop app, go to the Code tab and choose the folder you just
unzipped as the working folder. Claude reads the `CLAUDE.md` file inside it
automatically, so it already knows the rules: it prepares and checks orders,
and you place them.

## Step 3. Paste this and let Claude do the setup

Copy the whole paragraph below, paste it into the Claude Code chat, and press
Return:

> I just downloaded this folder and I have never used it before. Please set it
> up for me, one step at a time, waiting for me after each step. Check that
> Python 3.9 or newer is installed and tell me how to install it if it is not.
> Install the Python libraries from requirements.txt. Create my .env file from
> .env.example and lock it down. Then tell me exactly how to create a Kraken
> API key with order permissions only and no withdrawal, deposit, or transfer
> permissions, and how to open .env in a text editor so I can paste the keys in
> myself. Never ask me to paste keys into this chat. When I say the keys are
> saved, test with a public ticker command, then check my balance on the main
> account, and tell me what to try next.

Claude will run each command, show you what it did, and stop where you have to
do something yourself. It asks your permission before it runs each command.
Read what it wants to run, then say yes. That prompt is a feature, not a
nuisance.

The one step Claude cannot do for you is creating the key on Kraken's website.
That is Step 4.

## Step 4. Create your Kraken API key

An API key is a password that lets a program act on your Kraken account. We
make one with the smallest set of permissions that still lets it place orders.

1. Sign in to Kraken in your browser and go to https://www.kraken.com/u/security/api
2. Click **Create API key** (or **Add key**).
3. Give it a name you will recognize later, such as `kraken-trades`.
4. Under permissions, turn **on** only these:
   - Query funds (sometimes called "Query funds and balances")
   - Query open orders and trades
   - Query closed orders and trades
   - Create and modify orders
   - Cancel and close orders
5. Leave **off** everything to do with withdrawals, deposits, transfers,
   staking, earn, or exporting data. You can add "Withdraw funds" later if
   you want the optional withdraw commands (see "Optional. Withdrawals").
6. Optional but recommended: set an expiry date a few months out. You can
   always make a new key.
7. Click **Generate key**.

Kraken now shows you two long strings: the **API Key** and the **Private Key**.
Copy both somewhere safe right now, such as a password manager. Kraken will
never show you the Private Key again. If you lose it, delete the key and make a
new one.

Claude will have opened your `.env` file in a text editor. It looks like this:

```
KRAKEN_API_KEY=paste_your_api_key_here
KRAKEN_API_SECRET=paste_your_private_key_here
```

Replace `paste_your_api_key_here` with your API Key and
`paste_your_private_key_here` with your Private Key. No spaces around the
equals sign, no quotation marks. Leave the `_2` lines alone unless you are
setting up a second account (see below). Save and close the editor, then go
back to Claude and say the keys are saved.

Three rules about this file:

- Never paste your keys into a Claude Code chat, a screenshot, a Discord
  message, or an email. Claude never needs to see them. It only needs them to
  be in the file.
- Never send anyone your `.env` file.
- If a key ever leaks, go back to Kraken, delete that key, and make a new one.
  Then update `.env`.

## Step 5. Start using it

Once Claude has shown you your balance, you are set up. Things to try, in
plain English:

> What is the minimum order size for LUNAUSD?

> Set up a buy ladder for 10 SOL between $95 and $75, 5 levels, post-only, on
> main. Dry-run it and show me the output.

Claude will write the order file into `orders/`, run the dry-run, show you the
preflight and Kraken validation output, and then give you a command that ends
in `--live`. That command is yours. Open a terminal in the same folder, paste
the command, read the list of orders one more time, and type `CONFIRM`.

After it runs, go back to Claude and say:

> I placed them. Check open orders on main.

## Optional. A second Kraken account

The tool supports two accounts out of the box. Make a second API key on the
second account exactly as in Step 4, then fill in the two `_2` lines in `.env`:

```
KRAKEN_API_KEY_2=...
KRAKEN_API_SECRET_2=...
```

Use it with `--account second`. If you would rather call it something else,
ask Claude to rename it in `accounts.json`, or open that file and change the
word `second` to whatever you like, such as `roth` or `spouse`. Keep the name
short, lowercase, and with no spaces.

## Optional. Withdrawals

Skip this unless you want Claude to prepare withdrawals for you. Most members
never need it, and you can always withdraw on Kraken's website instead.

The withdraw commands are built in. They work only when your API key has
Kraken's "Withdraw funds" permission, so that checkbox on Kraken is your on and
off switch.

Kraken only lets an API key send to addresses you saved and confirmed by email
on its website. The key cannot add new ones. So if your key ever leaked, the
worst it could do is move funds to addresses on your own saved list. That is
why the rule is simple: **only save addresses you control**, such as your own
Ledger or Tangem wallet.

1. On Kraken's website, save every address you want to send to: Funding, then
   Withdraw, pick the asset, then Add address. Give each one a clear name such
   as `My Ledger XRP`, and confirm the email Kraken sends you. The tool can only
   send to addresses saved this way. It cannot add new ones.
2. Go to https://www.kraken.com/u/security/api, edit your `kraken-trades` key,
   and turn **on** "Withdraw funds". Save. Your key and `.env` stay exactly the
   same. For a second account, do the same on that account's key.
3. Back in Claude, say:

> List my saved withdrawal addresses on main.

From then on you can ask in plain English, such as "withdraw 100 XRP to My
Ledger XRP from main." Claude runs the dry-run and shows you the full address,
network, fee, and the amount that will arrive. Then it hands you a command
ending in `--live`. You run it and type the last 6 characters of the address to
confirm. Crypto withdrawals cannot be reversed, so read that address every
time.

To turn withdrawals off again, edit the key on Kraken and turn "Withdraw
funds" back off.

## The rules, one more time

- Claude never runs `--live`. You do, and you type `CONFIRM` yourself.
- Every order file gets a dry-run first. If the dry-run fails, fix the file and
  dry-run it again before anything goes live.
- Your key only has "Withdraw funds" if you turned it on, and it can only send
  to addresses you saved on Kraken. Only save addresses you control.
- Your `.env` file never leaves your computer and never goes into a chat.
- `split` never touches your account. It only writes a file.
- A shared move from anyone in the community is information, not a decision.
  Run every idea through your own allocation, sizing, plan, and prayer before
  it becomes an order file.

## Troubleshooting

Paste any error into Claude first. It can usually fix it. The common ones:

| What you see | What it means | What to do |
|--------------|---------------|------------|
| `command not found: python3` | Python is not installed, or on Windows it is called `python` | Install Python from https://www.python.org/downloads/ (on Windows tick **Add Python to PATH**) |
| `No module named krakenex` | The libraries are not installed | Ask Claude to run `pip3 install -r requirements.txt` |
| `Account 'main' needs KRAKEN_API_KEY and KRAKEN_API_SECRET set in .env` | No `.env` file, or the placeholders are still in it, or the wrong folder is open | Do Step 4, and check the folder in the Code tab |
| `EAPI:Invalid key` | The key or secret was pasted wrong, or the two are swapped | Reopen `.env` and paste again. API Key on the KEY line, Private Key on the SECRET line |
| `EGeneral:Permission denied` | The key is missing a permission | Edit the key on Kraken and turn on the permissions listed in Step 4 |
| `EAPI:Invalid nonce` | Two programs used the key at the same instant | Wait a few seconds and run it again |
| `EOrder:Insufficient funds` | Not enough balance for the order | Check `balance` and shrink the order |
| `Account mismatch` | The order file says one account and `--account` says another | Regenerate the file for the right account |
| `permission denied. Withdrawals are off` | Your key does not have "Withdraw funds" | Only if you want them, follow "Optional. Withdrawals" above |
| `No saved ... withdrawal address named ...` | The `--to` name does not match a saved address exactly | Ask Claude to list your withdrawal addresses and copy the name exactly |
| `below Kraken's minimum withdrawal` | Each network has a minimum | Send more, or wait until you hold more |
| `NotOpenSSLWarning` on a Mac | A harmless Python warning | Ignore it |
| Claude says it will not run `--live` | It is doing its job | Run the command yourself |

## When you are done with the tool

If you stop using it, go to https://www.kraken.com/u/security/api and delete
the key. You can always make a new one later.

*This is not financial advice. Always take everything back to the Lord in prayer for personal confirmation.*
