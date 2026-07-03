"""Unit tests: rules math, formation picking, transfer constraints, predictions.

Run: python3 -m pytest tests/ -q   (or python3 -m unittest discover tests)
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wcfantasy import optimize, predict, rules
from wcfantasy.models import Player


def mk(pid, pos, price=5.0, form=5.0, country="AAA", status="playing", ms="start", own=10.0):
    return Player(id=pid, first_name=f"P{pid}", last_name="", known_name=None,
                  squad_id=hash(country) % 1000, position=pos, price=price, status=status,
                  match_status=ms, ownership=own, round_points={}, total_points=int(form * 4),
                  avg_points=form, form=form, country=country, country_abbr=country)


def mk_squad():
    ps = []
    pid = 0
    for pos, n in rules.SQUAD_SHAPE.items():
        for i in range(n):
            pid += 1
            ps.append(mk(pid, pos, form=3 + i, country=f"C{pid % 7}"))
    return ps


class TestRules(unittest.TestCase):
    def test_transfer_hits(self):
        self.assertEqual(rules.transfer_hit(4, "R16"), 0)
        self.assertEqual(rules.transfer_hit(5, "R16"), -3)
        self.assertEqual(rules.transfer_hit(7, "R16"), -9)
        self.assertEqual(rules.transfer_hit(15, "R32"), 0)   # unlimited window
        self.assertEqual(rules.transfer_hit(7, "FINAL"), -3)  # 6 free

    def test_formations(self):
        self.assertTrue(rules.valid_formation(5, 3, 2))
        self.assertFalse(rules.valid_formation(6, 3, 1))
        self.assertFalse(rules.valid_formation(2, 6, 2))

    def test_validate_squad_shape_and_country_cap(self):
        squad = mk_squad()
        self.assertEqual(rules.validate_squad(squad, "R16"), [])
        for p in squad[:5]:
            p.country_abbr = "XXX"
        probs = rules.validate_squad(squad, "R16")   # 5 > cap of 4
        self.assertTrue(any("XXX" in s for s in probs))

    def test_budget_violation_detected(self):
        squad = mk_squad()
        for p in squad:
            p.price = 8.0  # 120 > 105
        self.assertTrue(any("budget" in s for s in rules.validate_squad(squad, "R16")))


class TestXI(unittest.TestCase):
    def test_best_xi_legal_and_captained(self):
        squad = mk_squad()
        ep = {p.id: {"ep": p.form} for p in squad}
        xi = optimize.best_xi(squad, ep)
        self.assertEqual(len(xi.starters), 11)
        self.assertIn(xi.formation, rules.FORMATIONS)
        self.assertEqual(sum(1 for p in xi.starters if p.position == "GK"), 1)
        # captain is the top-EP outfielder and EP counts him twice
        best_out = max((p for p in xi.starters if p.position != "GK"), key=lambda p: ep[p.id]["ep"])
        self.assertEqual(xi.captain.id, best_out.id)
        base = sum(ep[p.id]["ep"] for p in xi.starters)
        self.assertAlmostEqual(xi.ep, base + ep[xi.captain.id]["ep"], places=2)

    def test_eliminated_player_never_starts_over_alive(self):
        squad = mk_squad()
        ep = {p.id: {"ep": 0.0 if i == 3 else p.form} for i, p in enumerate(squad)}
        xi = optimize.best_xi(squad, ep)
        self.assertEqual(len([p for p in xi.starters]), 11)


class TestPredict(unittest.TestCase):
    def test_poisson_conceded_after_first(self):
        self.assertAlmostEqual(predict._expected_conceded_after_first(0.0), 0.0, places=6)
        mu = 1.5
        expected = sum(math.exp(-mu) * mu**k / math.factorial(k) * (k - 1) for k in range(2, 12))
        self.assertAlmostEqual(predict._expected_conceded_after_first(mu), expected, places=9)

    def test_advance_prob_symmetry(self):
        self.assertAlmostEqual(predict.advance_prob(2000, 2000), 0.5)
        self.assertGreater(predict.advance_prob(2120, 1520), 0.95)

    def test_news_out_zeroes_p_play(self):
        p = mk(1, "MID")
        p.news = {"status": "out", "note": "injured"}
        p_play, _, why = predict.p_play_and_minutes(p)
        self.assertEqual(p_play, 0.0)
        self.assertIn("out", why)

    def test_expected_goals_monotonic_in_elo(self):
        cfg = {"weights": {"base_mu": 1.3, "elo_scale": 1000.0}}
        strong = predict.expected_goals(2100, 1600, cfg)
        weak = predict.expected_goals(1600, 2100, cfg)
        self.assertGreater(strong, 1.3)
        self.assertLess(weak, 1.3)


if __name__ == "__main__":
    unittest.main()
