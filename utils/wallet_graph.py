"""Graphe de transactions - identification du wallet de convergence (pattern brouillage)."""
from __future__ import annotations   # compat 3.9 pour la syntaxe `X | None`
import time
from collections import defaultdict
from typing import Dict, List


class WalletGraph:
    def __init__(self, window_sec: int = 600, dust_threshold: float = 0.05):
        self.window_sec = window_sec
        self.dust_threshold = dust_threshold
        self.edges: Dict[tuple, List[dict]] = defaultdict(list)
        self.nodes: Dict[str, dict] = {}

    def add_node(self, wallet: str, role: str = "node", ts: float | None = None) -> None:
        if wallet not in self.nodes:
            self.nodes[wallet] = {"role": role, "ts": ts if ts is not None else time.time()}

    def add_edge(self, src: str, dst: str, amount: float, sig: str, ts: float | None = None) -> None:
        t = ts if ts is not None else time.time()
        self.add_node(src, ts=t)
        self.add_node(dst, ts=t)
        self.edges[(src, dst)].append({"amount": amount, "sig": sig, "ts": t})

    def find_convergence_wallet(self, min_in: int = 3) -> str | None:
        """Wallet recevant des fonds de >= min_in sources distinctes et renvoyant peu."""
        sources_per_dst: Dict[str, set] = defaultdict(set)
        for (src, dst), txs in self.edges.items():
            if any(t["amount"] >= self.dust_threshold for t in txs):
                sources_per_dst[dst].add(src)
        best, best_count = None, 0
        for dst, sources in sources_per_dst.items():
            out = sum(1 for (s, _d) in self.edges if s == dst)
            if len(sources) >= min_in and out <= 2 and len(sources) > best_count:
                best, best_count = dst, len(sources)
        return best

    def prune_expired(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        for key in list(self.edges.keys()):
            self.edges[key] = [t for t in self.edges[key] if now - t["ts"] <= self.window_sec]
            if not self.edges[key]:
                del self.edges[key]
        for wallet in list(self.nodes.keys()):
            if now - self.nodes[wallet]["ts"] > self.window_sec:
                del self.nodes[wallet]
