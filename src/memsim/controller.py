"""A command-level DRAM controller: bank state machines, timing constraints, scheduling, refresh.

Each channel is simulated on its own (channels share nothing). Time is in clock
cycles and only advances to the next moment something can happen, so idle
stretches cost nothing.

Per cycle the controller may issue **one command** (the command bus), chosen
from the commands its queued requests need next:

* the request's row is open in its bank -> a column command (RD or WR);
* the bank is closed -> ACT (open the row);
* another row is open -> PRE (close it), unless a queued request still hits that row.

A command is *ready* when every timing constraint it is subject to (timing.py)
is met. The scheduler picks among ready commands:

* ``fcfs``   only the oldest request may issue (strict arrival order);
* ``frfcfs`` First-Ready, First-Come-First-Served (Rixner et al., ISCA 2000):
  ready column commands (row hits) first, then the oldest ready command.

Page policy ``open`` leaves a row open after an access; ``closed`` precharges it
(auto-precharge) unless another queued request hits it. Refresh: every REFI
cycles a rank precharges all banks and is busy for RFC cycles.

Every issued command can be logged; ``checker.check`` re-verifies the log
against the timing rules independently of this code.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .mapping import AddressMapping
from .timing import Timing

NEVER = -(10 ** 18)


@dataclass
class Request:
    addr: int
    write: bool = False
    arrival: int = 0                # cycle the request reaches the controller
    id: int = 0
    # filled in by the controller
    ch: int = 0
    ra: int = 0
    bg: int = 0
    ba: int = 0
    ro: int = 0
    issue: int = -1                 # cycle of its column command
    done: int = -1                  # cycle its last data beat is on the bus
    kind: str = "hit"               # hit | miss (bank closed) | conflict (another row open)


@dataclass
class Command:
    t: int
    cmd: str                        # ACT PRE RD WR REF ; PREA is an auto-precharge (no command-bus slot)
    ra: int
    bg: int = -1
    ba: int = -1
    ro: int = -1


class _Bank:
    __slots__ = ("row", "act_ok", "col_ok", "pre_ok", "act_for")

    def __init__(self):
        self.row = -1           # open row, -1 when precharged
        self.act_ok = 0         # earliest ACT (PRE + RP, ACT + RC, refresh)
        self.col_ok = 0         # earliest RD/WR (ACT + RCD)
        self.pre_ok = 0         # earliest PRE (ACT + RAS, RD + RTP, WR end + WR)
        self.act_for = -1       # request that caused the last ACT


class _Rank:
    """Per-rank history. Same-bank-group constraints (RRD_L, CCD_L, WTR_L) need the last event
    in *each* bank group, not just the last event in the rank: a read to group 0 must respect a
    write to group 0 even if a write to group 1 came after it (a bug Hypothesis found)."""

    __slots__ = ("acts", "last_act", "act_bg", "last_col", "col_bg", "wr_end", "wr_end_bg",
                 "next_ref", "ref_until")

    def __init__(self, refi: int, bankgroups: int):
        self.acts: deque = deque(maxlen=4)
        self.last_act, self.act_bg = NEVER, [NEVER] * bankgroups      # last ACT: any group, per group
        self.last_col, self.col_bg = NEVER, [NEVER] * bankgroups
        self.wr_end, self.wr_end_bg = NEVER, [NEVER] * bankgroups     # end of write data
        self.next_ref = refi
        self.ref_until = 0


@dataclass
class ChannelStats:
    cycles: int = 0
    commands: dict = field(default_factory=lambda: {"ACT": 0, "PRE": 0, "PREA": 0, "RD": 0, "WR": 0, "REF": 0})
    busy_data: int = 0


class Channel:
    def __init__(self, t: Timing, scheduler: str = "frfcfs", page: str = "open", queue: int = 32,
                 refresh: bool = True, log: bool = False):
        if scheduler not in ("fcfs", "frfcfs"):
            raise ValueError(scheduler)
        if page not in ("open", "closed"):
            raise ValueError(page)
        self.t, self.sched, self.page, self.qcap, self.refresh = t, scheduler, page, queue, refresh
        self.banks = [[[_Bank() for _ in range(t.banks)] for _ in range(t.bankgroups)] for _ in range(t.ranks)]
        self.ranks = [_Rank(t.REFI, t.bankgroups) for _ in range(t.ranks)]
        self.bus_free = 0
        self.bus_dir = ""
        self.log: list[Command] | None = [] if log else None
        self.st = ChannelStats()
        self.pending: dict[tuple, dict[int, int]] = {}    # (ra, bg, ba) -> {row: queued requests}

    def _hits_queued(self, ra: int, bg: int, ba: int, row: int) -> bool:
        return self.pending.get((ra, bg, ba), {}).get(row, 0) > 0

    def _enqueue(self, queue: list, r: Request) -> None:
        queue.append(r)
        d = self.pending.setdefault((r.ra, r.bg, r.ba), {})
        d[r.ro] = d.get(r.ro, 0) + 1

    def _dequeue(self, queue: list, r: Request) -> None:
        queue.remove(r)
        self.pending[(r.ra, r.bg, r.ba)][r.ro] -= 1

    # ── timing: earliest legal cycle for each command ───────────────
    def _bank(self, r: Request) -> _Bank:
        return self.banks[r.ra][r.bg][r.ba]

    def t_act(self, ra: int, bg: int, b: _Bank) -> int:
        t, rk = self.t, self.ranks[ra]
        e = max(b.act_ok, rk.ref_until, rk.last_act + t.RRD_S, rk.act_bg[bg] + t.RRD_L)
        if len(rk.acts) == 4:
            e = max(e, rk.acts[0] + t.FAW)
        return e

    def t_col(self, r: Request, b: _Bank) -> int:
        t, rk = self.t, self.ranks[r.ra]
        e = max(b.col_ok, rk.last_col + t.CCD_S, rk.col_bg[r.bg] + t.CCD_L)
        if r.write:
            bubble = t.RTRW if self.bus_dir == "r" else 0
            e = max(e, self.bus_free + bubble - t.CWL)
        else:
            e = max(e, rk.wr_end + t.WTR_S, rk.wr_end_bg[r.bg] + t.WTR_L, self.bus_free - t.CL)
        return e

    # ── issuing ─────────────────────────────────────────────────────
    def _emit(self, now: int, cmd: str, ra: int, bg: int = -1, ba: int = -1, ro: int = -1) -> None:
        self.st.commands[cmd] += 1
        if self.log is not None:
            self.log.append(Command(now, cmd, ra, bg, ba, ro))

    def _act(self, now: int, r: Request, b: _Bank) -> None:
        t, rk = self.t, self.ranks[r.ra]
        b.row, b.act_for = r.ro, r.id
        b.col_ok = now + t.RCD
        b.pre_ok = max(b.pre_ok, now + t.RAS)
        b.act_ok = now + t.RC
        rk.acts.append(now)
        rk.last_act = rk.act_bg[r.bg] = now
        self._emit(now, "ACT", r.ra, r.bg, r.ba, r.ro)

    def _pre(self, now: int, ra: int, bg: int, ba: int, b: _Bank, auto: bool = False) -> None:
        b.row = -1
        b.act_ok = max(b.act_ok, now + self.t.RP)
        self._emit(now, "PREA" if auto else "PRE", ra, bg, ba)

    def _col(self, now: int, r: Request, b: _Bank, queue: list) -> None:
        t, rk = self.t, self.ranks[r.ra]
        if r.write:
            start = now + t.CWL
            b.pre_ok = max(b.pre_ok, start + t.burst_cycles + t.WR)
            rk.wr_end = rk.wr_end_bg[r.bg] = start + t.burst_cycles
            self.bus_dir = "w"
        else:
            start = now + t.CL
            b.pre_ok = max(b.pre_ok, now + t.RTP)
            self.bus_dir = "r"
        self.bus_free = start + t.burst_cycles
        self.st.busy_data += t.burst_cycles
        rk.last_col = rk.col_bg[r.bg] = now
        r.issue, r.done = now, start + t.burst_cycles
        if b.act_for == r.id and r.kind == "hit":
            r.kind = "miss"
        self._emit(now, "WR" if r.write else "RD", r.ra, r.bg, r.ba, r.ro)
        self._dequeue(queue, r)
        if self.page == "closed" and not self._hits_queued(r.ra, r.bg, r.ba, r.ro):
            self._pre(b.pre_ok, r.ra, r.bg, r.ba, b, auto=True)   # auto-precharge, no command slot

    # ── the scheduling loop ─────────────────────────────────────────
    def run(self, reqs: list[Request]) -> list[Request]:
        """Serve requests (sorted by arrival). Returns them with issue/done/kind filled in."""
        queue: list[Request] = []
        i, n, now = 0, len(reqs), 0
        t = self.t
        while i < n or queue:
            while i < n and len(queue) < self.qcap and reqs[i].arrival <= now:
                self._enqueue(queue, reqs[i])
                i += 1
            best, best_key, wake = None, None, None

            def consider(e: int, key: tuple, action) -> None:
                nonlocal best, best_key, wake
                if e <= now:  # noqa: B023  (called only within this iteration)
                    if best_key is None or key < best_key:
                        best, best_key = action, key
                elif wake is None or e < wake:
                    wake = e

            # refresh: when due, the rank closes its banks and refreshes before anything else
            refreshing = set()
            if self.refresh:
                for ra, rk in enumerate(self.ranks):
                    if now >= rk.next_ref:
                        refreshing.add(ra)
                        open_banks = [(bg, ba, b) for bg, row in enumerate(self.banks[ra])
                                      for ba, b in enumerate(row) if b.row >= 0]
                        for bg, ba, b in open_banks:
                            consider(b.pre_ok, (-2, bg, ba), ("PRE", ra, bg, ba, b))
                        if not open_banks:
                            e = max([b.act_ok for row in self.banks[ra] for b in row] + [rk.ref_until])
                            consider(e, (-3,), ("REF", ra))
                    elif wake is None or rk.next_ref < wake:
                        wake = rk.next_ref

            cands = queue if self.sched == "frfcfs" else queue[:1]
            for k, r in enumerate(cands):
                if r.ra in refreshing:
                    continue
                b = self._bank(r)
                if b.row == r.ro:
                    consider(self.t_col(r, b), (0 if self.sched == "frfcfs" else 1, k), ("COL", r, b))
                elif b.row < 0:
                    consider(self.t_act(r.ra, r.bg, b), (1, k), ("ACT", r, b))
                elif self.sched == "fcfs" or not self._hits_queued(r.ra, r.bg, r.ba, b.row):
                    # FR-FCFS keeps a row open while queued requests still hit it
                    consider(b.pre_ok, (1, k), ("PRE", r.ra, r.bg, r.ba, b, r))

            if best is not None:
                kind = best[0]
                if kind == "COL":
                    self._col(now, best[1], best[2], queue)
                elif kind == "ACT":
                    self._act(now, best[1], best[2])
                elif kind == "PRE":
                    _, ra, bg, ba, b = best[:5]
                    if len(best) > 5:
                        best[5].kind = "conflict"
                    self._pre(now, ra, bg, ba, b)
                else:  # REF
                    ra = best[1]
                    rk = self.ranks[ra]
                    rk.ref_until = now + t.RFC
                    rk.next_ref += t.REFI
                    for row in self.banks[ra]:
                        for b in row:
                            b.act_ok = max(b.act_ok, now + t.RFC)
                    self._emit(now, "REF", ra)
                now += 1
                continue
            nxt = [w for w in (wake,) if w is not None]
            if i < n and len(queue) < self.qcap:
                nxt.append(reqs[i].arrival)
            now = max(now + 1, min(nxt)) if nxt else now + 1
        self.st.cycles = max([r.done for r in reqs], default=0)
        return reqs


# ── whole memory system ─────────────────────────────────────────────────
@dataclass
class Result:
    timing: Timing
    requests: list[Request]
    channels: list[Channel]

    @property
    def cycles(self) -> int:
        start = min(r.arrival for r in self.requests)
        return max(r.done for r in self.requests) - start

    def bytes(self) -> int:
        return len(self.requests) * self.timing.access_bytes

    def bandwidth_gbps(self) -> float:
        return self.bytes() / self.timing.ns(self.cycles)

    def efficiency(self) -> float:
        """Achieved bandwidth over the peak of the channels the requests touched."""
        used = len({r.ch for r in self.requests})
        return self.bandwidth_gbps() / (used * self.timing.channel_peak_gbps())

    def latency_ns(self) -> list[float]:
        return [self.timing.ns(r.done - r.arrival) for r in self.requests]

    def row_hit_rate(self) -> float:
        return sum(r.kind == "hit" for r in self.requests) / len(self.requests)

    def commands(self) -> dict:
        out: dict = {}
        for c in self.channels:
            for k, v in c.st.commands.items():
                out[k] = out.get(k, 0) + v
        return out

    def summary(self) -> dict:
        lat = sorted(self.latency_ns())
        n = len(lat)
        return {"requests": n, "bandwidth_GBps": self.bandwidth_gbps(), "efficiency": self.efficiency(),
                "latency_mean_ns": sum(lat) / n, "latency_p50_ns": lat[n // 2],
                "latency_p99_ns": lat[min(n - 1, (99 * n) // 100)], "row_hit_rate": self.row_hit_rate(),
                "kinds": {k: sum(r.kind == k for r in self.requests) for k in ("hit", "miss", "conflict")},
                "commands": self.commands()}


def simulate(t: Timing, reqs: list[Request], mapping: AddressMapping | None = None, scheduler: str = "frfcfs",
             page: str = "open", queue: int = 32, refresh: bool = True, log: bool = False) -> Result:
    """Decode addresses, split by channel, run each channel's controller."""
    mapping = mapping or AddressMapping(t)
    per: dict[int, list[Request]] = {}
    for k, r in enumerate(reqs):
        r.id = k
        d = mapping.decode(r.addr)
        r.ch, r.ra, r.bg, r.ba, r.ro = d.ch, d.ra, d.bg, d.ba, d.ro
        r.issue, r.done, r.kind = -1, -1, "hit"
        per.setdefault(d.ch, []).append(r)
    chans = []
    for ch in sorted(per):
        c = Channel(t, scheduler, page, queue, refresh, log)
        c.run(sorted(per[ch], key=lambda r: (r.arrival, r.id)))
        chans.append(c)
    return Result(t, reqs, chans)
