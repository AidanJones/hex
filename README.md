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

Python 3.10+, no dependencies.

```bash
git clone https://github.com/AidanJones/hex
cd hex
cp .env.example .env       # then add your API key
python -m hex
```

Settings live in `.env` (git-ignored); real environment variables and
command-line flags override it. Supported backends (`HEX_BACKEND`):

| backend | key | default model |
|---|---|---|
| `minimax` | `MINIMAX_API_KEY` | `MiniMax-M2.5` |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5-5` |
| `openai` | any OpenAI-compatible server (Ollama, LM Studio, llama.cpp) via `HEX_BASE_URL` | set `HEX_MODEL` |

Override the model with `HEX_MODEL` or `--model`. Reasoning models that print
`<think>` blocks are handled: the thinking is stripped before Hex reads the answer.

## Commands

Everything you type goes to the MUD, except lines starting with `/`:

| command | does |
|---|---|
| `/a [question]` | ask Hex; blank = "what's happening, what next?" |
| `/1` … `/5` | send that suggestion (your choice, your keypress) |
| `/auto` | toggle advice each time you enter a new room (rate-limited) |
| `/learn <topic>` | sends `help <topic>` and saves the page to `knowledge/cache/` |
| `/docs <query>` | search local notes, no LLM call |
| `/goal <text>` | tell it what you're aiming for ("join the witches", "earn money in AM") |
| `//text` | send a line that starts with `/` |

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
read and chose to send. Read `help rules` in game, and if you're unsure whether
something is OK, ask a creator. Please keep it that way in any fork.

## Roadmap

- [ ] Split-pane TUI (game left, Hex right) with Textual
- [ ] Use Discworld's `room.writtenmap` / map frames for "how do I get to X"
- [ ] Import selected pages from the community wiki into `knowledge/` (with credit)
- [ ] Explain mode: highlight any line and ask "what does this mean?"
- [ ] Session memory: a running journal of quests, NPCs and places across sessions
- [ ] Web front end (browser client + Hex panel)

## Prior art

- Quow's MUSHclient plugins: the community's standard Discworld client add-ons (no AI).
- [discMapper](https://github.com/iLPdev/discMapper) and
  [Mudlet DiscworldUI](https://wiki.mudlet.org/w/DiscworldUI): GMCP mapping/status for Mudlet.
- [mcp-telnet](https://github.com/distantorigin/mcp-telnet/): lets an LLM drive a telnet session.
- dikuclient's `/howto` (draft PR): LLM command help for Diku MUDs.

## Licence

MIT. Discworld is Terry Pratchett's; Discworld MUD is run by its volunteer
creators. This is an unofficial fan tool.
