# Automation rules (important for this tool)

Discworld is strict about scripting, partly because you earn XP just for being
online. Display-only client features (colours, gauges, gagging spam, sounds,
maps) are fine. Anything that sends commands to the MUD without you typing them
risks a ban. Read `help rules` in game and ask a creator if unsure.

This co-pilot therefore only advises. You choose and send every command,
including the numbered suggestions (/1, /2…), which are just a shortcut for
typing what you have read.

# GMCP

Discworld sends structured data (vitals, room info, map) over GMCP when the
client asks. `help gmcp` documents it, and the creators explicitly allow
clients to enable GMCP automatically at login.
For map frames: `options output map frame = on`.
