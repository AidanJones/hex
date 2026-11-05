# Notes for coding agents

- Stdlib only in `hex/` unless a dependency is clearly worth it (Textual for the TUI is the expected exception; keep it optional).
- Hard rule: nothing may send a command to the MUD without an explicit keypress from the human. No timers, triggers or "auto-run" for suggestions. Discworld bans automation.
- `help automation` also asks clients not to trivialise the game (solving puzzles, recommending the next step of a task). Don't add features that do; check `knowledge/cache/guide_concepts_automation.md` first.
- Never let text typed while the server has echo off (passwords) reach logs or the LLM. `GameState.hidden_input` handles this; keep the test.
- Run tests: `uv run python -m unittest discover -s tests -t .`
- Knowledge files: markdown under `knowledge/`; headings become retrieval chunks. Credit any wiki material imported.
- Don't fetch the community wiki (dwwiki.mooo.com): its robots.txt bars Claude's crawler. Official docs stay in the git-ignored `knowledge/cache/`; they're copyright with no reuse terms.
