# Alfred

A voice-only assistant with the manner of an English butler. Say **"Hey Alfred"**, hear a
chime, then just talk. You can interrupt him at any time. Say "Hey Alfred" on its own and
he greets you; say "Hey Alfred, what time is it?" in one breath and he just answers.

This version, **alfred-cascade**, hears you with Deepgram, thinks with GPT-6 Luna and speaks
with Cartesia. Its twin, **alfred-sts**, has the same features with GPT-Live 1, one
speech-to-speech model.

- **Questions and conversation:** Alfred answers straight away, and looks up anything
  current (weather, news, prices) on the web.
- **Bigger jobs:** research, comparisons, notes, drafts, charts and small programs run in
  the background while you carry on talking. A chime tells you when one is done, and Alfred
  tells you the result.
- **Email:** Alfred can send email from your Gmail account, but only after you say yes.
- **Memory:** Alfred remembers what matters about you ("I'm vegetarian") and what you've
  talked about and asked him to find.
- **Reminders:** "Remind me in 10 minutes to stretch", "every weekday at 8, brief me on
  the weather". Alfred speaks up when they're due, without "Hey Alfred".
- **Timers and alarms:** "a pasta timer for 8 minutes", "wake me at 6:30 on weekdays".
  They ring until you say "stop" (or "snooze"), and alarms ring even if Alfred isn't running.
- **Google Chat:** "tell the family chat I'm on my way". Alfred reads the message back and
  sends it only after you say yes.
- **The house:** with Wayne Manor running, Alfred controls its lights, temperature,
  telephone and Spotify: "turn the lights blue", "play some Glenn Miller", "turn it down",
  "make a call".
- **Web pages and games:** "make me a Flappy Bird game", then play it on your phone.

## What it sounds like

| Sound | Meaning |
| --- | --- |
| Wake chime | Alfred heard "Hey Alfred" and is listening |
| Soft two-note chime | A background job finished, a job needs your answer, or a reminder is due |
| Single soft tick | Alfred will stop listening in 5 seconds unless you speak |
| Low two-note sound | Alfred has stopped listening: say "Hey Alfred" next time |
| Two short low beeps | Alfred couldn't connect: check the internet connection |
| Three quick high beeps, repeating | A timer is done: say "stop" |
| Rising four-note chime, repeating | An alarm: say "stop" or "snooze" (9 minutes) |

Alfred stops listening after about 25 seconds of quiet. Speech he doesn't answer, such as
music with vocals or a TV, doesn't count, so he still goes to sleep when it's playing. While a job is still working or
waiting for your answer, he stays with you for up to 2 minutes.

To send him to sleep straight away, tell him so: "Alfred, go to sleep" or "you can sleep
now". He says goodnight and stops listening. "Thanks" or "goodbye" alone don't do it. Jobs
keep running, timers and alarms still ring, and "Hey Alfred" wakes him again.

## Things to try

- "Hey Alfred, what's the weather in Manila today?"
- "Compare three robot vacuums under 15,000 pesos and save notes."
- "How's my job going?", "What did you find?", "Stop that job."
- "Make me a chart of the peso against the US dollar over the last year."
- "Research the latest Grammys news and email it to me."
- "Remember that I'm vegetarian." "What do you know about me?" "Forget my address."
- "What did you find about ramen yesterday?"
- "Remind me at 3 pm to call Mum." "What reminders do I have?" "Cancel that reminder."
- "Set a timer for 10 minutes." "How long is left?" "Add 5 minutes." "Stop."
- "Wake me at 6:30 every weekday." "Snooze." "Cancel my alarm."
- "Tell the family chat I'll be home by seven."

Notes, charts and anything else Alfred makes are saved in `~/.alfred/workspace`.

## Memory and reminders

- **What Alfred knows about you** is in `~/.alfred/profile.md`, one fact per line. Read it,
  edit it, or ask him to forget something. After each conversation he notes anything
  lasting you mentioned, and keeps a short summary of what you talked about.
- **Reminders you set go off on time**, even at night. Ones that came due while Alfred was
  off are mentioned when he starts again (if they're from the last 12 hours); repeating
  ones simply carry on.
- **Quiet hours** (10 pm to 7 am by default): Alfred doesn't start talking on his own, and
  job results and questions wait until you next speak to him. Your reminders still go off.

## Timers and alarms

- A timer beeps for up to 30 seconds, an alarm rings for up to 2 minutes, until you say
  "stop" (or "snooze" for an alarm). The sound pauses while Alfred is speaking.
- **Alarms don't depend on Alfred.** Each one is also handed to the computer itself
  (launchd on a Mac, systemd on the Raspberry Pi). If Alfred has stopped or crashed, the
  computer plays the alarm a minute late; when Alfred is running he rings it himself and
  the computer stays silent. The computer has to be on and awake, and on a Mac you need to
  be logged in.
- Timers only run while Alfred does.

## Google Chat

Alfred can post to Google Chat spaces through their incoming webhooks. In Google Chat,
open the space, then **Apps & integrations → Webhooks → Add webhook**, and copy the
address. Add one line per space to `.env`, named after how you'll say it:

```
ALFRED_GOOGLE_CHAT_DEFAULT=https://chat.googleapis.com/v1/spaces/...
ALFRED_GOOGLE_CHAT_FAMILY=https://chat.googleapis.com/v1/spaces/...
```

"Tell the family chat…" uses `FAMILY`; with no space named, Alfred uses `DEFAULT` (or the
only one). He always reads the message back first, and nothing is sent unless you say yes
within a minute. Messages appear as posted by the webhook's app, not by you. Treat these
addresses like passwords: anyone with one can post to that space.

## Web pages, games and small tools

Alfred can make things you open in a browser: "make me a Flappy Bird game", "a loan
calculator for a 2-million-peso house", "a chart of the peso against the dollar this year".
Turn it on in `.env`:

```
ALFRED_WEB_PORT=8080
```

When it's ready, Alfred tells you the address, such as `http://raspy.local:8080/flappy-bird/`.
Open it on any phone or laptop on the same Wi-Fi; `http://raspy.local:8080/` lists
everything he has made. They're saved in `~/.alfred/workspace/www/`.

- Only that folder is shared, read-only. Anyone on your Wi-Fi can open it, so keep it for
  home use.
- Pages run in the browser: games, calculators, lists, charts and slides work; anything
  that needs a program running on the computer (a database, a chat server) doesn't.
- Not quite right? "Make the bird fall slower" edits it; reload the page.
- On a Mac, allow incoming connections the first time macOS asks.

## Wayne Manor (the house)

Wayne Manor, a separate project, is a virtual smart home with its own 3D page. Start it,
then point Alfred at it in `.env`:

```
ALFRED_WAYNE_MANOR_URL=http://127.0.0.1:8765/mcp
```

Alfred connects at the start of each conversation; if Wayne Manor isn't running, he
carries on without it and says he can't reach the house. House commands take a few
seconds, like a web look-up. Music plays in Spotify on the Mac running Wayne Manor: if
that's also Alfred's speaker, keep the volume moderate so he doesn't hear the music as
speech. "Make a call" uses the house's telephone; Alfred can't place real phone calls.

## Staying in control

- **You approve anything that leaves the house.** Before sending an email, Alfred reads out
  the recipient, the subject and how the email begins, and waits for your "yes". Anything
  but a clear yes is a no, and no answer within 5 minutes is a no. Google Chat messages
  are read back the same way (no answer within a minute is a no).
- **Alfred asks rather than guesses** when a request is unclear.
- **Background jobs work in their own folder.** They can't read your personal folders,
  your passwords or Alfred's settings, can't make payments or unlock anything, and can't
  install software on your computer without asking.
- **Every action is recorded.** `~/.alfred/audit.jsonl` lists everything a job did or
  tried to do, and what happened, including every Google Chat message.
- **Stopping:** say "stop that job" at any time. Ctrl+C stops Alfred; unfinished jobs carry
  on when you start him again.

## Setup (macOS)

You need:

- [uv](https://docs.astral.sh/uv/);
- an OpenAI API key with access to `gpt-6-luna`;
- a [Deepgram](https://console.deepgram.com) API key (speech to text);
- a [Cartesia](https://play.cartesia.ai) API key (Alfred's voice);
- a Claude sign-in for background jobs: your Claude plan (`claude setup-token`) or a
  Claude API key;
- optional, for email: a Gmail address and a
  [Gmail app password](https://myaccount.google.com/apppasswords) (never your real password).

```bash
cd alfred-cascade
uv sync
cp .env.example .env    # then fill in your keys
```

The first time you run Alfred, macOS asks for microphone access for your terminal app.
Allow it (System Settings → Privacy & Security → Microphone).

## Run

```bash
uv run alfred
```

Wait for `Waiting for "Hey Alfred"...`, then speak. Press Ctrl+C to stop.

## Settings

All settings live in `.env`; `.env.example` lists them with explanations. The ones you are
most likely to change:

| Setting | What it does |
| --- | --- |
| `ALFRED_TTS_VOICE` | Alfred's voice: a Cartesia voice ID. The default is Archie, a calm British man; browse others at play.cartesia.ai |
| `ALFRED_WAKE_THRESHOLD` | Raise it if Alfred wakes by mistake; lower it if he misses you |
| `ALFRED_IDLE_SECONDS` | How long Alfred waits in silence before he stops listening |
| `ALFRED_QUIET_HOURS` | When Alfred stays quiet unless a reminder is due, e.g. `22-7`, or `off` |
| `ALFRED_HOME`, `ALFRED_UNITS`, `ALFRED_CURRENCY` | Where you are: Alfred assumes this place, `metric` or `imperial` units, and this currency unless you say otherwise |
| `ALFRED_TIMEZONE` | Your time zone (`Asia/Manila`), used for reminders and quiet hours whatever the computer is set to |
| `ALFRED_GOOGLE_CHAT_<NAME>` | A Google Chat space's webhook address, one line per space (see Google Chat) |
| `ALFRED_WEB_PORT` | Share web pages Alfred makes on your home network, e.g. `8080`. Empty: off |
| `ALFRED_WAYNE_MANOR_URL` | Wayne Manor's address, e.g. `http://127.0.0.1:8765/mcp`. Empty: no house |
| `ALFRED_INPUT_DEVICE`, `ALFRED_OUTPUT_DEVICE` | Which microphone and speaker to use (list them with `uv run python -m sounddevice`) |
| `ALFRED_TASK_BUDGET_USD` | The most one background job may cost |

## Costs

Talking to Alfred is billed by three services: Deepgram for each minute it listens,
OpenAI for the words GPT-6 Luna reads and writes, and Cartesia for the words Alfred speaks.
A background job typically costs a few cents and never more than `ALFRED_TASK_BUDGET_USD`.
Check your usage in each service's dashboard.

## Privacy

Your voice goes to Deepgram while Alfred is listening; the words go to OpenAI, and his
replies to Cartesia to be spoken. Background jobs run on Claude.
Nothing is sent before you say "Hey Alfred", except when Alfred speaks up himself to ask
you something, and messages you approve go to Google Chat. After each conversation, its transcript goes to Claude once, to note anything worth
remembering; his memory keeps only those notes. Everything Alfred keeps (what he knows
about you, conversation summaries, jobs, reminders, the action record and saved files)
stays in `~/.alfred` on your computer. The detailed logs in `logs/` also record what was
said; delete them whenever you like.

## Starting fresh

Stop Alfred first (Ctrl+C, or `systemctl --user stop alfred` on the Pi), then:

```bash
uv run alfred-reset            # everything he keeps: files, memory, jobs, reminders, logs
uv run alfred-reset files      # only files jobs made (notes, code, web pages)
uv run alfred-reset web        # only the shared web pages
uv run alfred-reset profile    # only what he knows about you
uv run alfred-reset logs       # only the logs
```

It lists what it will delete and asks first (add `--yes` to skip the question). Your
settings (`.env`) and the code are never touched.

## Troubleshooting

Every run writes a detailed log to `logs/alfred-<date>-<time>.log`; logs older than two
weeks are deleted automatically. If something goes wrong, the newest log shows what
happened.

- **Two short low beeps after "Hey Alfred":** Alfred couldn't reach Deepgram or OpenAI.
  Check the internet connection and the keys in `.env`.
- **"Alfred is already running":** another copy is open; stop it first.

- **Alfred hears himself or interrupts himself:** use headphones, or lower the speaker volume.
- **Alfred wakes by mistake, or doesn't hear "Hey Alfred":** adjust `ALFRED_WAKE_THRESHOLD`.
- **Background jobs fail at once:** check the Claude sign-in in `.env`.
- **Email fails:** check `ALFRED_EMAIL` and the app password in `.env`.
