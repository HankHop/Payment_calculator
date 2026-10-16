import unittest
from datetime import date

from paycalc import config, data, taxes
from paycalc.engine import analyze
from paycalc.schedule import build_events, pay_dates
from paycalc.simulate import make_plan, plan_from_config, simulate

FED = data.federal(2026)


def cfg_from(**overrides):
    raw = {
        "filing_status": "single",
        "state": "TX",
        "pay": {"base_salary": 100000, "frequency": "biweekly", "first_pay_date": "2026-01-02"},
        "contributions": {"pretax_percent": 0, "posttax_percent": 0},
    }
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(raw.get(k), dict):
            raw[k] = {**raw[k], **v}
        else:
            raw[k] = v
    return config.parse(raw)


class TestTaxes(unittest.TestCase):
    def test_federal_single_100k(self):
        # taxable 83,900 = 1,240 + 38,000*.12 + 33,500*.22
        self.assertAlmostEqual(taxes.federal_income_tax(100000, 0, "single", FED), 13170.0, places=2)

    def test_federal_pretax_reduces_taxable(self):
        self.assertAlmostEqual(taxes.federal_taxable(100000, 10000, "single", FED), 73900.0)

    def test_federal_mfj_and_hoh_standard_deduction(self):
        self.assertEqual(taxes.federal_taxable(50000, 0, "mfj", FED), 17800)
        self.assertEqual(taxes.federal_taxable(50000, 0, "hoh", FED), 25850)

    def test_fica_below_wage_base(self):
        f = taxes.fica(100000, "single", FED)
        self.assertAlmostEqual(f["social_security"], 6200.0)
        self.assertAlmostEqual(f["medicare"], 1450.0)

    def test_fica_above_wage_base_and_addl_medicare(self):
        f = taxes.fica(250000, "single", FED)
        self.assertAlmostEqual(f["social_security"], 184500 * 0.062)
        self.assertAlmostEqual(f["medicare"], 250000 * 0.0145 + 50000 * 0.009)
        self.assertAlmostEqual(taxes.fica(250000, "mfj", FED)["medicare"], 250000 * 0.0145)

    def test_marginal_rate_boundaries(self):
        b = FED["brackets"]["single"]
        self.assertEqual(taxes.marginal_rate(12399.99, b), 0.10)
        self.assertEqual(taxes.marginal_rate(12400, b), 0.12)
        self.assertEqual(taxes.marginal_rate(-5, b), 0.0)

    def test_state_flat_with_standard_deduction(self):
        st = data.state(2026, "CO")
        self.assertAlmostEqual(taxes.state_income_tax(100000, 0, "single", st), (100000 - 16100) * 0.044)

    def test_state_no_tax(self):
        self.assertEqual(taxes.state_income_tax(500000, 0, "single", data.state(2026, "TX")), 0.0)

    def test_state_nj_and_pa_tax_401k_deferrals(self):
        pa = data.state(2026, "PA")
        self.assertAlmostEqual(taxes.state_income_tax(100000, 20000, "single", pa), 100000 * 0.0307)
        self.assertEqual(taxes.state_marginal(100000, 20000, "single", pa), 0.0)
        co = data.state(2026, "CO")
        self.assertAlmostEqual(taxes.state_income_tax(100000, 20000, "single", co), (80000 - 16100) * 0.044)

    def test_state_credit_exemption(self):
        ar = data.state(2026, "AR")  # 2% to 4,600 then 3.9%, minus $29 credit
        taxable = 100000 - 2470
        expected = 4600 * 0.02 + (taxable - 4600) * 0.039 - 29
        self.assertAlmostEqual(taxes.state_income_tax(100000, 0, "single", ar), expected)

    def test_state_zero_bracket_floor(self):
        mo = data.state(2026, "MO")
        self.assertEqual(taxes.state_income_tax(16100 + 1000, 0, "single", mo), 0.0)

    def test_every_state_computes(self):
        states = data._load("states", 2026)["states"]
        self.assertEqual(len(states), 50 + 1 - 0)  # 50 states + DC
        for code, st in states.items():
            for status in ("single", "mfj", "mfs", "hoh"):
                t = taxes.state_income_tax(150000, 10000, status, st)
                self.assertGreaterEqual(t, 0.0, code)
                self.assertLess(t, 150000 * 0.15, code)


class TestSchedule(unittest.TestCase):
    def test_biweekly_26_and_27(self):
        self.assertEqual(len(pay_dates(2026, "biweekly", date(2026, 1, 2))), 26)
        self.assertEqual(len(pay_dates(2026, "biweekly", date(2026, 1, 1))), 27)

    def test_first_pay_date_outside_year_is_aligned(self):
        d = pay_dates(2026, "biweekly", date(2026, 10, 16))
        self.assertTrue(all(x.year == 2026 for x in d))
        self.assertIn(date(2026, 10, 16), d)

    def test_other_frequencies(self):
        self.assertEqual(len(pay_dates(2026, "semimonthly", None)), 24)
        self.assertEqual(len(pay_dates(2026, "monthly", None)), 12)
        self.assertEqual(pay_dates(2026, "monthly", None)[1], date(2026, 2, 28))
        self.assertEqual(len(pay_dates(2026, "weekly", date(2026, 1, 2))), 52)

    def test_raise_applies_from_effective_date(self):
        cfg = cfg_from(pay={"raise": {"effective_date": "2026-07-01", "new_salary": 130000}})
        events = build_events(cfg)
        before = [e for e in events if e.pay_date < date(2026, 7, 1)]
        after = [e for e in events if e.pay_date >= date(2026, 7, 1)]
        self.assertAlmostEqual(before[0].gross, 100000 / 26)
        self.assertAlmostEqual(after[0].gross, 130000 / 26)

    def test_bonus_percent_uses_salary_in_effect(self):
        cfg = cfg_from(pay={"raise": {"effective_date": "2026-03-01", "new_salary": 110000},
                            "bonus": {"pay_date": "2026-04-10", "percent_of_salary": 10}})
        bonus = [e for e in build_events(cfg) if e.kind == "bonus"][0]
        self.assertAlmostEqual(bonus.gross, 11000)


class TestSimulation(unittest.TestCase):
    def test_bonus_excluded_from_contributions_and_match(self):
        cfg = cfg_from(
            pay={"bonus": {"pay_date": "2026-03-13", "amount": 20000}},
            contributions={"pretax_percent": 10, "posttax_percent": 0,
                           "employer": {"match_rate_percent": 100, "match_up_to_percent_of_pay": 5,
                                        "nonelective_percent": 2}})
        r = simulate(build_events(cfg), make_plan(cfg, FED, 10, 0))
        self.assertAlmostEqual(r.gross, 120000)
        self.assertAlmostEqual(r.pretax, 10000)      # 10% of 100k, none of the bonus
        self.assertAlmostEqual(r.match, 5000)        # 100% up to 5% of 100k
        self.assertAlmostEqual(r.nonelective, 2000)

    def test_bonus_included_when_flagged(self):
        cfg = cfg_from(
            pay={"bonus": {"pay_date": "2026-03-13", "amount": 20000}},
            contributions={"pretax_percent": 10, "posttax_percent": 0,
                           "bonus_eligible_for_employee_contributions": True,
                           "employer": {"match_rate_percent": 100, "match_up_to_percent_of_pay": 5,
                                        "bonus_eligible_for_match": True}})
        r = simulate(build_events(cfg), make_plan(cfg, FED, 10, 0))
        self.assertAlmostEqual(r.pretax, 12000)
        self.assertAlmostEqual(r.match, 6000)

    def test_deferral_bonus_but_not_match_eligible(self):
        cfg = cfg_from(
            pay={"bonus": {"pay_date": "2026-03-13", "amount": 20000}},
            contributions={"pretax_percent": 10, "posttax_percent": 0,
                           "bonus_eligible_for_employee_contributions": True,
                           "employer": {"match_rate_percent": 100, "match_up_to_percent_of_pay": 5}})
        r = simulate(build_events(cfg), make_plan(cfg, FED, 10, 0))
        self.assertAlmostEqual(r.pretax, 12000)
        self.assertAlmostEqual(r.match, 5000)

    def test_employee_limit_stops_deferrals(self):
        cfg = cfg_from(pay={"base_salary": 200000},
                       contributions={"pretax_percent": 30, "posttax_percent": 0})
        r = simulate(build_events(cfg), make_plan(cfg, FED, 30, 0))
        self.assertAlmostEqual(r.employee, 24500)
        self.assertIsNotNone(r.limit_reached_on)
        self.assertLess(r.limit_reached_on, date(2026, 12, 1))

    def test_catch_up_raises_limit(self):
        cfg = cfg_from(age_end_of_year=55, pay={"base_salary": 200000},
                       contributions={"pretax_percent": 30, "posttax_percent": 0})
        self.assertAlmostEqual(simulate(build_events(cfg), make_plan(cfg, FED, 30, 0)).employee, 32500)
        cfg = cfg_from(age_end_of_year=61, pay={"base_salary": 200000})
        self.assertEqual(make_plan(cfg, FED, 0, 0).employee_limit, 35750)

    def test_compensation_limit(self):
        cfg = cfg_from(pay={"base_salary": 500000},
                       contributions={"pretax_percent": 1, "posttax_percent": 0,
                                      "employer": {"nonelective_percent": 10}})
        r = simulate(build_events(cfg), make_plan(cfg, FED, 1, 0))
        self.assertAlmostEqual(r.pretax, 3600)
        self.assertAlmostEqual(r.nonelective, 36000)


class TestRecommendation(unittest.TestCase):
    def _analysis(self, **kw):
        base = dict(
            state="TX",
            pay={"base_salary": 120000},
            contributions={"pretax_percent": 5, "posttax_percent": 0,
                           "employer": {"match_rate_percent": 50, "match_up_to_percent_of_pay": 6}})
        base.update(kw)
        return analyze(cfg_from(**base))

    def test_hits_target_without_exceeding_limit(self):
        a = self._analysis()
        e = a.recommended.result.employee
        self.assertLessEqual(e, 24500 + 0.01)
        self.assertGreater(e, 24500 - 1.0 * 120000 / 100 - 0.01)   # within one percent step

    def test_split_follows_marginal_rate(self):
        # 22% fed bracket, TX: pretax while 22% > assumed retirement rate
        hi_ret = self._analysis(recommendation={"expected_retirement_marginal_rate_percent": 35})
        self.assertEqual(hi_ret.recommended.plan.pretax_percent, 0)
        self.assertGreater(hi_ret.recommended.plan.posttax_percent, 0)
        lo_ret = self._analysis(recommendation={"expected_retirement_marginal_rate_percent": 10})
        self.assertEqual(lo_ret.recommended.plan.posttax_percent, 0)
        self.assertGreater(lo_ret.recommended.plan.pretax_percent, 0)

    def test_mixed_split_when_deduction_crosses_bracket(self):
        # 130k single: taxable 113,900 sits in the 24% bracket (starts at 105,700).
        # With an assumed retirement rate of 23%, only the 8,200 above the 22% bracket saves more than 23%.
        a = self._analysis(pay={"base_salary": 130000},
                           recommendation={"expected_retirement_marginal_rate_percent": 23})
        rec = a.recommendation
        self.assertAlmostEqual(rec.pretax_dollars_target, 8200, delta=1)
        self.assertGreater(a.recommended.plan.pretax_percent, 0)
        self.assertGreater(a.recommended.plan.posttax_percent, 0)

    def test_bonus_ineligible_raises_percentage(self):
        no_bonus = self._analysis(pay={"base_salary": 100000})
        with_bonus = self._analysis(pay={"base_salary": 100000,
                                         "bonus": {"pay_date": "2026-03-13", "amount": 50000}})
        tot = lambda a: a.recommended.plan.pretax_percent + a.recommended.plan.posttax_percent
        self.assertEqual(tot(no_bonus), tot(with_bonus))   # bonus excluded -> same eligible pay
        incl = self._analysis(pay={"base_salary": 100000,
                                   "bonus": {"pay_date": "2026-03-13", "amount": 50000}},
                              contributions={"bonus_eligible_for_employee_contributions": True})
        self.assertLess(tot(incl), tot(no_bonus))

    def test_plan_max_warning(self):
        a = self._analysis(pay={"base_salary": 40000}, recommendation={"plan_max_percent": 30})
        self.assertTrue(any("Plan maximum" in w for w in a.warnings))

    def test_state_that_taxes_deferrals_gives_no_state_benefit(self):
        a = self._analysis(state="PA", recommendation={"expected_retirement_marginal_rate_percent": 22})
        # fed 22% only (PA saves nothing): 22% > 22% is false -> post-tax
        self.assertEqual(a.recommended.plan.pretax_percent, 0)


def after_tax_cfg(**overrides):
    base = dict(
        state="TX",
        pay={"base_salary": 150000},
        contributions={"posttax_type": "after_tax",
                       "elections": [{"effective_date": "2026-01-02", "pretax_percent": 10, "posttax_percent": 0},
                                     {"effective_date": "2026-08-07", "pretax_percent": 10, "posttax_percent": 30}],
                       "employer": {"match_rate_percent": 50, "match_up_to_percent_of_pay": 6,
                                    "nonelective_percent": 3}})
    base.update(overrides)
    return cfg_from(**base)


class TestAfterTax(unittest.TestCase):
    def test_after_tax_does_not_use_elective_limit(self):
        cfg = after_tax_cfg()
        r = simulate(build_events(cfg), plan_from_config(cfg, FED))
        self.assertAlmostEqual(r.pretax, 15000)                 # 10% of 150k, under 24,500
        self.assertGreater(r.posttax, 0)
        self.assertAlmostEqual(r.elective, r.pretax)            # after-tax not counted as elective

    def test_election_change_applies_from_effective_date(self):
        cfg = after_tax_cfg()
        r = simulate(build_events(cfg), plan_from_config(cfg, FED))
        before = [x for x in r.rows if x.pay_date < date(2026, 8, 7)]
        after = [x for x in r.rows if x.pay_date >= date(2026, 8, 7)]
        self.assertTrue(all(x.posttax == 0 for x in before))
        self.assertTrue(all(x.posttax > 0 for x in after))

    def test_match_only_on_pretax_in_after_tax_mode(self):
        cfg = after_tax_cfg()
        r = simulate(build_events(cfg), plan_from_config(cfg, FED))
        self.assertAlmostEqual(r.match, 0.5 * 0.06 * 150000)    # 10% pretax >= 6% cap
        self.assertAlmostEqual(r.nonelective, 0.03 * 150000)

    def test_total_cap_stops_after_tax(self):
        cfg = after_tax_cfg(pay={"base_salary": 400000})
        plan = plan_from_config(cfg, FED)
        r = simulate(build_events(cfg), plan)
        self.assertLessEqual(r.total_additions, 72000 + 0.01)
        self.assertIsNotNone(r.total_cap_reached_on)

    def test_catch_up_raises_total_cap(self):
        cfg = after_tax_cfg(age_end_of_year=55)
        self.assertEqual(make_plan(cfg, FED, 0, 0).total_cap, 80000)

    def test_take_home_cap_limits_after_tax(self):
        cfg = after_tax_cfg(contributions={"posttax_type": "after_tax", "elections": [
            {"effective_date": "2026-01-02", "pretax_percent": 20, "posttax_percent": 80}]})
        a = analyze(cfg)
        # 100% of gross cannot be deducted; taxes still have to be paid.
        self.assertLess(a.current.result.posttax, 0.8 * 150000)
        self.assertTrue(any("take-home" in w for w in a.warnings))
        for r in a.current.result.rows:
            self.assertGreaterEqual(r.gross - r.pretax - r.posttax, 0)

    def test_recommendation_after_as_of_hits_total_target(self):
        cfg = after_tax_cfg(as_of_date="2026-07-01")
        a = analyze(cfg)
        rec = a.recommendation
        self.assertEqual(rec.effective_from, date(2026, 7, 3))
        # paychecks on or before as_of keep the actual elections
        cur_rows = {r.pay_date: r for r in a.current.result.rows}
        for r in rec.result.rows:
            if r.pay_date <= date(2026, 7, 1):
                self.assertAlmostEqual(r.posttax, cur_rows[r.pay_date].posttax)
                self.assertAlmostEqual(r.pretax, cur_rows[r.pay_date].pretax)
        self.assertLessEqual(rec.result.total_additions, 72000 + 0.01)
        # rounding down to a whole percent leaves less than 1% of remaining pay unfilled
        remaining_pay = 150000 / 26 * 13
        self.assertGreater(rec.result.total_additions, 72000 - 0.01 * remaining_pay - 1)

    def test_recommendation_flags_unreachable_target(self):
        a = analyze(after_tax_cfg(as_of_date="2026-10-16"))   # 5 paychecks left, ~$30k short
        self.assertTrue(any("take-home" in w for w in a.warnings))
        self.assertLess(a.recommendation.result.total_additions, 72000)

    def test_full_year_from_the_start(self):
        # No as_of_date: the recommendation replaces all elections from the first paycheck.
        a = analyze(after_tax_cfg())
        rec = a.recommendation
        self.assertEqual(rec.effective_from, date(2026, 1, 2))
        self.assertLessEqual(rec.result.total_additions, 72000 + 0.01)
        self.assertGreater(rec.result.total_additions, 72000 - 0.01 * 150000 - 1)
        self.assertAlmostEqual(rec.result.pretax, 24500, delta=0.01 * 150000)
        self.assertEqual({r.pay_date for r in rec.result.rows if r.posttax > 0},
                         {r.pay_date for r in rec.result.rows if r.kind == "regular"})

    def test_recommendation_pretax_fills_remaining_elective(self):
        cfg = after_tax_cfg(as_of_date="2026-06-30")
        a = analyze(cfg)
        self.assertLessEqual(a.recommendation.result.pretax, 24500 + 0.01)
        self.assertGreater(a.recommendation.result.pretax, 24500 - 0.01 * 150000 - 1)

    def test_no_remaining_paychecks(self):
        a = analyze(after_tax_cfg(as_of_date="2026-12-31"))
        self.assertTrue(any("No paychecks remain" in w for w in a.warnings))

    def test_legacy_single_percent_config_still_works(self):
        cfg = cfg_from(contributions={"pretax_percent": 5, "posttax_percent": 5})
        self.assertEqual(len(cfg.contributions.elections), 1)
        self.assertEqual(cfg.contributions.posttax_type, "roth")


class TestConfigValidation(unittest.TestCase):
    def test_bad_filing_status(self):
        with self.assertRaises(config.ConfigError):
            cfg_from(filing_status="married")

    def test_unknown_state(self):
        with self.assertRaises(config.ConfigError):
            analyze(cfg_from(state="ZZ"))

    def test_bonus_needs_exactly_one_amount(self):
        with self.assertRaises(config.ConfigError):
            cfg_from(pay={"bonus": {"pay_date": "2026-03-13"}})

    def test_date_outside_tax_year(self):
        with self.assertRaises(config.ConfigError):
            cfg_from(pay={"raise": {"effective_date": "2027-01-01", "new_salary": 1}})


if __name__ == "__main__":
    unittest.main()
