# Connecting

Host `discworld.starturtle.net`, port 23 (or 4242 if 23 is blocked).
There is also a browser client on the official site. The community-recommended
desktop setup is MUSHclient with Quow's plugin pack (minimap, speedwalking,
NPC/shop search, mission timers).

# Where new characters start

New characters start in a tutorial area (Pumpkin Town). It teaches quests,
guilds, etiquette, NPC interaction, fighting and shops. Do its quests: they
give starting levels in several skill trees. Tutorial money converts to local
currency when you leave. Before leaving you choose a nationality, which sets
your start city and native language. Most new players pick Ankh-Morpork.

# Looking around

- `look` (or `l`) describes the room; `look <thing>` looks at anything named in it.
  Nearly every noun in a Discworld room description can be looked at.
- `glance` gives a short room view.
- `map` shows a small map of nearby rooms.
- `inventory` (`i`), `score`, `skills`, `stats` describe your character.
- `help <topic>` is the in-game manual; `help` alone lists topics.

# The parser

Discworld is an LP MUD: every object can add its own commands, so the game
generally needs full verbs and clear nouns. "pl" will not mean "place".
It understands fairly natural English: `give second apple to john`,
`get two apples and three pears from backpack`. Use the alias system (`help alias`)
to make your own short forms for things you type often.

# Talking

- `say <text>` to people in the room, `tell <name> <text>` privately.
- The newbie channel is the place to ask questions; players are generally
  quick and friendly. Check `help newbie` / `help channels` for exact syntax.
