"""Minimal async telnet client with GMCP support, tuned for Discworld MUD.

Stdlib only. Handles IAC negotiation, subnegotiation, GA/EOR prompt markers,
and the ECHO option (so we know when the server is asking for a password).
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Callable

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
GA, EOR_CMD = 249, 239
OPT_ECHO, OPT_EOR, OPT_GMCP = 1, 25, 201

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


@dataclass
class Event:
    """One parsed thing from the server."""
    kind: str                 # "text" | "gmcp" | "prompt" | "echo_off" | "echo_on"
    text: str = ""
    package: str = ""
    data: object = None


class TelnetParser:
    """Byte-stream state machine. Feed bytes, get Events and replies to send."""

    def __init__(self) -> None:
        self.state = "data"
        self.buf = bytearray()
        self.sb = bytearray()
        self.cmd = 0
        self.gmcp_on = False

    def _gmcp_ready(self, events: list[Event]) -> None:
        if not self.gmcp_on:            # say hello once, however the server offers it
            self.gmcp_on = True
            events.append(Event("gmcp_ready"))

    def feed(self, data: bytes) -> tuple[list[Event], bytes]:
        events: list[Event] = []
        out = bytearray()

        def flush_text() -> None:
            if self.buf:
                events.append(Event("text", self.buf.decode("utf-8", "replace")))
                self.buf.clear()

        for b in data:
            st = self.state
            if st == "data":
                if b == IAC:
                    self.state = "iac"
                else:
                    self.buf.append(b)
            elif st == "iac":
                if b == IAC:
                    self.buf.append(IAC)
                    self.state = "data"
                elif b in (WILL, WONT, DO, DONT):
                    self.cmd = b
                    self.state = "opt"
                elif b == SB:
                    self.sb.clear()
                    self.state = "sb"
                elif b in (GA, EOR_CMD):
                    flush_text()
                    events.append(Event("prompt"))
                    self.state = "data"
                else:
                    self.state = "data"
            elif st == "opt":
                out += self._negotiate(self.cmd, b, events)
                self.state = "data"
            elif st == "sb":
                if b == IAC:
                    self.state = "sb_iac"
                else:
                    self.sb.append(b)
            elif st == "sb_iac":
                if b == SE:
                    flush_text()
                    ev = self._subneg(bytes(self.sb))
                    if ev:
                        events.append(ev)
                    self.state = "data"
                else:  # IAC IAC inside SB, or malformed
                    self.sb.append(b)
                    self.state = "sb"
        flush_text()
        return events, bytes(out)

    def _negotiate(self, cmd: int, opt: int, events: list[Event]) -> bytes:
        if cmd == WILL:
            if opt == OPT_ECHO:      # server will echo => it's hiding our input (password)
                events.append(Event("echo_off"))
                return bytes([IAC, DO, opt])
            if opt == OPT_GMCP:          # the normal way: server offers, client accepts
                self._gmcp_ready(events)
                return bytes([IAC, DO, opt])
            if opt == OPT_EOR:
                return bytes([IAC, DO, opt])
            return bytes([IAC, DONT, opt])
        if cmd == WONT:
            if opt == OPT_ECHO:
                events.append(Event("echo_on"))
            return b""
        if cmd == DO:
            if opt == OPT_GMCP:
                self._gmcp_ready(events)
                return bytes([IAC, WILL, opt])
            return bytes([IAC, WONT, opt])
        return b""  # DONT: nothing to do

    @staticmethod
    def _subneg(payload: bytes) -> Event | None:
        if not payload or payload[0] != OPT_GMCP:
            return None
        body = payload[1:].decode("utf-8", "replace")
        pkg, _, raw = body.partition(" ")
        try:
            data = json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError:
            data = raw
        return Event("gmcp", package=pkg.lower(), data=data)


def gmcp_frame(package: str, data: object = None) -> bytes:
    body = package if data is None else f"{package} {json.dumps(data)}"
    return bytes([IAC, SB, OPT_GMCP]) + body.encode() + bytes([IAC, SE])


# Packages Discworld documents in `help gmcp`. No version suffixes: this is the
# form the community's working MUSHclient plugin sends.
DISCWORLD_GMCP = ["char.login", "char.vitals", "char.info",
                  "room.info", "room.map", "room.writtenmap"]


@dataclass
class MudConnection:
    host: str = "discworld.starturtle.net"
    port: int = 23
    on_event: Callable[[Event], None] = lambda e: None
    reader: asyncio.StreamReader | None = field(default=None, repr=False)
    writer: asyncio.StreamWriter | None = field(default=None, repr=False)
    parser: TelnetParser = field(default_factory=TelnetParser)

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)

    async def run(self) -> None:
        assert self.reader and self.writer
        while data := await self.reader.read(4096):
            events, reply = self.parser.feed(data)
            if reply:
                self.writer.write(reply)
            for ev in events:
                if ev.kind == "gmcp_ready":
                    self.writer.write(gmcp_frame("Core.Hello", {"client": "dw-hex", "version": "0.1"}))
                    self.writer.write(gmcp_frame("Core.Supports.Set", DISCWORLD_GMCP))
                self.on_event(ev)
            await self.writer.drain()

    def send(self, line: str) -> None:
        assert self.writer
        # UTF-8 never produces a 0xFF byte, so no IAC escaping is needed.
        self.writer.write(line.encode("utf-8") + b"\r\n")

    def close(self) -> None:
        if self.writer:
            self.writer.close()
