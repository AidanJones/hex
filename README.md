# Hex

*Anthill Inside.*

Play [Discworld MUD](http://discworld.starturtle.net/lpc/) with Hex at your
elbow. Named after the Unseen University's thinking engine, Hex is an LLM that
reads what the game shows you, the structured GMCP data, and a local library of
help pages and notes, then **explains what's going on and suggests what to type
next**. You stay in charge. It never sends a command on its own.

Why: Discworld is wonderful and famously hard to get into. Commands need full
words, systems are spread across in-game help, a wiki and old fan sites, and
new players often wander without a sense of what to do next. Hex is a patient,
well-read friend for those first weeks.

```
You are in the Pumpkin Town square. A pumpkin-headed man sells maps here.
Exits: north, east and west.
/a what should I do first?
┌─ Hex ─────────────────────────────
│ You're in the tutorial town square. The map seller is worth a look.
│ /1  look man          — see what the seller offers
│ /2  map               — get your bearings
│ /3  help newbie       — how to ask other players for help
└────────────────────────────────────────
/1
→ look man
```

## Quick start

Python 3.10+ and [uv](https://docs.astral.sh/uv/). Hex itself has no dependencies;
the split-screen interface needs [Textual](https://textual.textualize.io/), which
`uv run` installs for you.

```bash
git clone https://github.com/AidanJones/hex
cd hex
cp .env.example .env       # then add your API key
uv run python -m hex
```

Without uv: `pip install textual` (optional), then `python -m hex`.

With Textual you get the game on the left and, on the right, a minimap with the
room name and exits, HP/GP bars, and Hex's latest advice. The password box hides
itself the moment the game asks. Below 90 columns the right side folds away.
Without Textual, or with `--plain`, Hex is a plain scrolling terminal.

If the map stays empty, type `/gmcp` to see which packets have arrived, and try
`options output map frame = on` in game.

## What Hex knows

- **Official guides:** `uv run python -m hex.guides` saves Discworld's newbie guides and key
  concept pages to `knowledge/cache/` (about 2 minutes, `--all` for every concept
  page). They're copyright Discworld MUD, so they stay in that git-ignored folder.
- **In-game help:** Hex can offer a help page as a suggestion. Choosing it reads the
  page and saves it, the same as `/learn`.
- **Its own notes:** Hex records lasting facts it sees in `hex-notes.md`. See them with
  `/notes`, and remove any with `/forget <title>`. Other players' words reach it too,
  so check these now and then.
- **Where you've been:** every room you walk through goes into `rooms.db`. `/route
  <place>` shows the way, one step at a time, using only moves you've made.
- **Your character name:** saved to `player.txt` when you log in, and shown when you
  connect or type `/name`. Your password is never saved.
- **Your own material:** drop `.md` files into `knowledge/`. The community wiki isn't
  imported automatically, because its `robots.txt` bars Claude's crawler by name.

Settings live in `.env` (git-ignored); real environment variables and
command-line flags override it. Supported backends (`HEX_BACKEND`):

| backend | key | default model |
|---|---|---|
| `minimax` | `MINIMAX_API_KEY` | `MiniMax-M3` |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5-5` |
| `openai` | any OpenAI-compatible server (Ollama, LM Studio, llama.cpp) via `HEX_BASE_URL` | set `HEX_MODEL` |

Override the model with `HEX_MODEL` or `--model`. Reasoning models that print
`<think>` blocks are handled: the thinking is stripped before Hex reads the answer.

## Commands

Everything you type goes to the MUD, except lines starting with `/`:

| command | does |
|---|---|
| `/a [question]` | ask Hex; blank = "what's happening, what next?" |
| `/1` … `/5` | send that suggestion (your choice, your keypress); F1–F5 do the same in the split screen |
| `/auto` | toggle advice each time you enter a new room (rate-limited) |
| `/learn <topic>` | sends `help <topic>` and saves the page to `knowledge/cache/` |
| `/docs <query>` | search local notes, no LLM call |
| `/goal <text>` | tell it what you're aiming for ("join the witches", "earn money in AM") |
| `/route [place]` | show the way to somewhere you've been; `/1` takes the next step |
| `/notes`, `/forget <title>` | list or remove Hex's own notes |
| `/gmcp` | list the GMCP packets received so far |
| `//text` | send a line that starts with `/` |

In the split screen, F6 asks Hex, Up/Down recall commands, PageUp/PageDown scroll
the game text and Ctrl+Q quits. Tab accepts the grey completion, which draws on
Hex's suggestions, exits, your history, words in recent game text and known help
pages. It only fills in the box; you still press Enter. Drag with the mouse to select
text, and Ctrl+C copies it.

## How it works

```
 MUD ──telnet/GMCP──▶ telnet.py ──▶ state.py (transcript + latest GMCP)
                                         │
 knowledge/*.md ──▶ knowledge.py (BM25) ─┼─▶ advisor.py ──▶ llm.py ──▶ summary + /1../5
                                         │
 you ──────────────────────── cli.py ◀───┘
```

- **telnet.py**: stdlib asyncio client. Negotiates GMCP (option 201), announces
  the packages Discworld documents in `help gmcp`, and spots the server's echo-off
  (password prompt).
- **state.py**: rolling, ANSI-stripped transcript. Anything typed while echo is
  off is recorded as `<hidden>` and never reaches the LLM.
- **knowledge.py**: markdown files chunked by heading, ranked with BM25. Grows as
  you `/learn` topics, and you can drop your own notes in.
- **advisor.py**: one prompt with goals, retrieved notes, GMCP state and recent
  text; asks for JSON (summary / explain / suggestions / watch_out).

## Staying within the rules

Discworld does not allow bots or triggers that send commands for you. This tool
is built around that: the LLM only advises, and every command is something you
read and chose to send.

**That may not be enough on its own.** `help automation` also asks players not to
use scripts or plugins that trivialise the game, and its examples include tools
that read the game's output and recommend your next action. Hex is close to that
line, so ask a liaison about it before relying on it. Hex is told not to solve
puzzles or plan out repetitive tasks, but that is an instruction to a model, not a
guarantee. Please keep all of this in any fork.

## Roadmap

- [x] Split-pane TUI (game left, Hex right) with Textual
- [x] Room database and routes for "how do I get to X"
- [ ] Import selected pages from the community wiki into `knowledge/` (with credit)
- [ ] Explain mode: highlight any line and ask "what does this mean?"
- [x] Session memory: Hex's own notes, plus the room database
- [ ] Web front end (browser client + Hex panel)

## Prior art

- Quow's MUSHclient plugins: the community's standard Discworld client add-ons (no AI).
- [discMapper](https://github.com/iLPdev/discMapper) and
  [Mudlet DiscworldUI](https://wiki.mudlet.org/w/DiscworldUI): GMCP mapping/status for Mudlet.
- [mcp-telnet](https://github.com/distantorigin/mcp-telnet/): lets an LLM drive a telnet session.
- dikuclient's `/howto` (draft PR): LLM command help for Diku MUDs.
- [rgrams/discworld_mud_plugins](https://github.com/rgrams/discworld_mud_plugins):
  the source for how Hex decodes `room.map` and `char.vitals`.

## Licence

MIT. Discworld is Terry Pratchett's; Discworld MUD is run by its volunteer
creators. This is an unofficial fan tool.
