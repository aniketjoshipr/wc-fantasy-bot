# FIFA World Cup Fantasy 2026 — auto-recommender

Recommendation engine for the official game at https://play.fifa.com/fantasy/.
Every run it pulls FIFA's own public data feeds, predicts expected points (EP)
for all ~1,500 players, and tells you exactly which transfers to make, who to
captain, which XI/bench to set, and which booster to consider. You apply the
moves in the app (no login/credentials needed — FIFA's team-write API requires
a browser session and automating it risks your account).

## Data sources (all free, no keys)
- `play.fifa.com/json/fantasy/players.json` — prices, ownership %, per-round fantasy points, form, lineup signal (`matchStatus`), eliminated flags
- `play.fifa.com/json/fantasy/rounds.json` — rounds, deadlines, fixtures, live scores, scorers/assists
- ESPN `site.api.espn.com/.../soccer/fifa.world` — optional lineups for live mode
- `data/news.json` — injury/suspension/sentiment layer, refreshed by `bin/update-news.sh` (headless Claude web-search) or edited by hand
- `data/elo.json` — team strength ratings (editable)

## Commands
```bash
python3 -m wcfantasy recommend            # pre-deadline plan (+ dashboard/index.html)
python3 -m wcfantasy auto                 # cron mode: emails only when something is due
python3 -m wcfantasy compare              # predicted vs actual score per round
python3 -m wcfantasy live                 # matchday: catch starters who didn't play
python3 -m wcfantasy players <name>       # look up anyone's EP/form/price
python3 -m wcfantasy squad                # show your saved team
python3 -m wcfantasy squad --apply-plan 0 # sync squad.json after you applied plan 0 in the app
python3 -m wcfantasy email-test           # verify email credentials work
python3 -m wcfantasy fetch                # force-refresh feeds
python3 -m unittest discover tests       # test suite
bin/serve.sh                              # dashboard at http://localhost:8077
```

## The model (rule-based, transparent)
`EP = p(reach round) x p(plays) x [0.55 x form-signal + 0.45 x stats-model] + differential bonus EV`
- **form-signal**: FIFA's recent fantasy-points-per-round, scaled by opponent difficulty
- **stats-model**: Elo -> Poisson expected goals both ways -> goal/assist rates x scoring rules, clean-sheet probability, GK saves, cards, appearance
- **p(plays)**: FIFA's lineup signal (`start`/`sub`/`not_in_squad`) overridden by `news.json` (`out`/`doubt`/`p_play`)
- **p(reach round)**: for teams whose previous match hasn't finished yet (TBD bracket slots)
- Transfers: beam search over swap combos; extra transfers beyond the free allowance cost −3 each and are only proposed if EP gain beats the hit. Country caps (4/team in R16 → 8 in final), budget ($105m) and formation legality enforced.
- Tune weights in `data/config.json`, team strength in `data/elo.json`.

## Round workflow
1. **Day before deadline** (deadline = first kickoff of the round): `bin/run.sh daily`
   → updates news via Claude web-search, prints/pushes the plan, writes `dashboard/index.html`.
2. Apply the transfers/captain/bench in the app, then `python3 -m wcfantasy squad --apply-plan N`.
3. **During matchdays**: `bin/run.sh live` every ~30 min → alerts if a starter's match
   finished with them unused so you can decide on a manual sub (it nets the cost of
   losing auto-subs/VC fallback for you).

## Automation (cloud — survives the laptop being off)
GitHub Actions on the private repo **aniketjoshipr/wc-fantasy-bot** runs
`wcfantasy auto` on a schedule (`.github/workflows/auto.yml`): hourly baseline +
every 20 min during match hours, **July 1–20 only** (nothing runs — and no email
can send — after the tournament). SMTP creds live in GitHub Secrets. Each run
commits `data/state.json` / `predictions.json` / `reports/` / `dashboard/` back
to the repo, so `git pull` shows you everything the bot did.

`auto` reads the real deadlines from FIFA's feed and only acts when relevant:
- **~30h before a deadline** → "early look" recommendation email
- **~8h before** → "FINAL CALL" email
- **during matches** → email only if one of your starters' match finished without playing
- **after a round completes** → settles predicted-vs-actual and emails the comparison

State in `data/state.json` prevents duplicate emails (delete a round's entry from
`sent` to force a resend). The local crontab entry has been removed — run
`bin/run.sh auto|recommend|live` manually whenever you like, and after changing
`squad.json`/`news.json`/`elo.json` run **`bin/sync.sh`** so the cloud runner
uses your latest team. Caveat: the cloud can't run the headless-Claude news
refresh, so injury news updates happen when you run it locally (or edit
`data/news.json` and sync).

## Email / Telegram credentials
Fill in `~/.config/wcfantasy.env` (chmod 600, outside the repo) — instructions are
in the file itself. Email goes through Brevo's free SMTP relay (300/day, no card):
sign up, copy the SMTP login + key from https://app.brevo.com/settings/keys/smtp,
then verify with `./bin/run.sh email-test`. Any other SMTP relay works too
(SMTP_HOST/PORT/USER/PASSWORD). Telegram (optional): bot token from @BotFather +
your chat id.

## Where recommendations end up
- **Terminal**: `./bin/run.sh recommend` prints the full colorized report
- **Archive**: every run is saved to `reports/<STAGE>-<timestamp>.txt` (and `reports/latest.txt`)
- **Cron output**: `less -R /tmp/wcfantasy.log`
- **Dashboard**: `dashboard/index.html` / http://localhost:8077 (`bin/serve.sh`)
- **Email/Telegram**: pushed at the ~30h and ~8h pre-deadline marks once credentials are set
- **Predictions log**: `data/predictions.json`, viewed with `./bin/run.sh compare`

## Predicted vs actual
Every pre-deadline run logs your saved lineup's predicted EP to `data/predictions.json`;
once FIFA marks the round complete, `auto` (or `compare`) fills in the actual points
(captain doubled, VC fallback if captain blanked) and emails the delta. Auto-subs are
not simulated — treat small drifts as noise, big ones as model feedback.

## Dashboard (local web app)
`dashboard/index.html` is regenerated on every run — either open the file directly
or run `bin/serve.sh` and open http://localhost:8077 (reload after each run).

## Files you own
- `data/squad.json` — your 15, captain, bench order, boosters still available.
  **Verify the booster flags** — the tool can't see which you've already used.
- `data/news.json`, `data/elo.json` — steer the model.

## Key game rules baked in (R16 onward)
- 4 free transfers before R16 and QF, 5 before SF, 6 before the Final; −3 pts per extra
- Max 4 players per country in R16 (5 QF, 6 SF, 8 Final); budget $105m; prices never change
- Captain doubles; VC fallback + auto-subs only fire if you make NO manual change that round
- Boosters (one per round, single use): Wildcard, 12th Man, Maximum Captain, Qualification (+2/starter whose team advances), Clean-Sheet Shield (CS survives 1 goal)
- Differential bonus: +2 when a <5%-owned player scores 4+
