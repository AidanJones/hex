# Notes for coding agents

- Stdlib only in `hex/` unless a dependency is clearly worth it (Textual for the TUI is the expected exception; keep it optional).
- Hard rule: nothing may send a command to the MUD without an explicit keypress from the human. No timers, triggers or "auto-run" for suggestions. Discworld bans automation.
- Never let text typed while the server has echo off (passwords) reach logs or the LLM. `GameState.hidden_input` handles this; keep the test.
- Run tests: `python -m unittest discover -s tests -t .`
- Knowledge files: markdown under `knowledge/`; headings become retrieval chunks. Credit any wiki material imported.
