# Payment_calculator

Estimates 2026 federal, state and FICA taxes for one wage earner and recommends pretax and
post-tax (Roth or after-tax) 401(k) contribution percentages. Python 3.9+, standard library only.

## Run

```
cp examples/example_config.json config.json     # config.json is git-ignored
python -m paycalc config.json                   # summary: current vs recommended
python -m paycalc config.json --schedule        # plus contributions on every pay date
python -m paycalc config.json --json            # machine-readable
python -m unittest discover -s tests            # tests
```

## Inputs (config.json)

| Field | Meaning |
|---|---|
| `filing_status` | `single`, `mfj`, `mfs`, `hoh` |
| `state` | Two-letter code (50 states + DC) |
| `age_end_of_year` | Optional. 50+ adds the catch-up limit, 60-63 the super catch-up |
| `pay.base_salary`, `frequency`, `first_pay_date` | `weekly`, `biweekly`, `semimonthly` (15th and month end), `monthly` (month end). `first_pay_date` is any real pay date, required for weekly/biweekly; 27-paycheck years are handled |
| `pay.raise` | `effective_date` plus `new_salary` or `percent`. Applies to pay dates on or after the effective date |
| `pay.bonus` | `pay_date` plus `amount` or `percent_of_salary` (of the salary in effect that day). Modeled as its own paycheck |
| `contributions.elections` | List of `{effective_date, pretax_percent, posttax_percent}`. Each applies to pay dates on or after its date, so a mid-year change is two entries. A single `pretax_percent`/`posttax_percent` pair (no list) applies all year |
| `contributions.posttax_type` | `after_tax` = non-Roth after-tax money (counts toward the 415(c) total limit, not the 24,500 deferral limit; mega-backdoor style). `roth` (default) = Roth 401(k), shares the deferral limit with pretax |
| `as_of_date` | Optional. Paychecks on or before it keep your actual elections; the recommendation applies to later paychecks only. Leave it out to plan the whole year from the first paycheck |
| `contributions.bonus_eligible_for_employee_contributions` | Whether your % is taken from the bonus |
| `employer.match_rate_percent`, `match_up_to_percent_of_pay` | e.g. 50 and 6 = 50% of your first 6% |
| `employer.nonelective_percent` | Unmatched employer contribution, % of eligible pay |
| `employer.bonus_eligible_for_match`, `bonus_eligible_for_nonelective` | Whether those are paid on the bonus |
| `recommendation.target_employee_contribution` | Pretax/Roth target: `"max"` (IRS limit incl. catch-up) or a dollar amount |
| `recommendation.target_total_additions` | `after_tax` mode only: `"max"` (415(c) limit, 72,000 + catch-up), a dollar amount, or `"none"` |
| `recommendation.expected_retirement_marginal_rate_percent` | Your guess at your combined retirement tax rate (default 22) |
| `recommendation.percent_step`, `plan_max_percent` | What your plan accepts (default 1% steps, 100% max) |

## How the recommendation works

Pretax / Roth (elective) money
1. Needed = IRS limit (or your dollar target) minus what is already contributed as of `as_of_date`.
2. Percent = needed / remaining eligible pay (after the raise, with or without the bonus per your
   flag), rounded down to the plan step so the limit is not hit early and match is not lost.
3. `roth` mode only: pretax is used while the combined federal + state marginal rate saved on the
   last pretax dollar is above `expected_retirement_marginal_rate_percent` (your assumption); the
   rest is Roth. `after_tax` mode: all elective dollars are pretax.

After-tax money (`posttax_type: after_tax`)
1. Room = 415(c) limit minus everything already contributed or projected: pretax, employer match
   and unmatched, and after-tax already paid.
2. Percent = room / remaining eligible pay, rounded down, then capped at what take-home pay can
   cover after pretax contributions and estimated taxes. If the cap binds, the tool says how far
   short the year ends up. Elections that add to 100% of gross can never be fully deducted.

Planning from the start of the year: omit `as_of_date` (or set it before your first paycheck) and
the recommendation covers every paycheck. Set it to a later date and earlier paychecks are treated
as already done.

## Tax data

`paycalc/data/federal_2026.json` (IRS brackets, standard deductions, FICA, 401(k) limits) and
`paycalc/data/states_2026.json` (Tax Foundation, as of Jan 1 2026). To add a year, add
`federal_<year>.json` and `states_<year>.json` and set `tax_year`.

## Not modeled

- Taxes are computed on this salary alone. Spouse income, other income, dependents, credits,
  itemized deductions, HSA/FSA/health premiums, and other pretax deductions are not included.
- Local taxes and state payroll programs (SDI, PFML, etc.).
- State specifics not in the Tax Foundation table: e.g. Alabama and Oregon federal tax
  deductions, Connecticut exemption phase-out, Utah taxpayer credit, state-level
  differences for `mfs` and `hoh` (single brackets and deductions are used for both).
  Per-state notes are printed when relevant.
- NJ and PA do tax 401(k) deferrals; this is modeled (no state benefit from pretax there).
- Employer match is computed per paycheck with no annual true-up, and only on pretax/Roth
  deferrals (not on after-tax money). If you hit the deferral limit early, match stops.
- The 415(c) limit is treated as a hard stop for pretax, employer and after-tax money together.
- Take-home cap uses the year's average tax rate; actual withholding differs paycheck to paycheck.
- Earnings and the tax on converting after-tax money to Roth are not modeled.
- 2026 rule requiring catch-up contributions to be Roth for higher earners.
- Tax is a liability estimate, not paycheck withholding.

Verify the state tables against your state's own instructions before relying on them.
