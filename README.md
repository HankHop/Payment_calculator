# Payment_calculator

Estimates 2026 federal, state and FICA taxes for one wage earner and recommends pretax and
post-tax (Roth or after-tax) 401(k) contribution percentages. Python 3.9+, standard library only.

## Run the GUI

```
python -m paycalc.gui            # opens http://127.0.0.1:8765/ in your browser
python -m paycalc.gui --port 9000 --config my_inputs.json --no-browser
```

A form on the left, results on the right, recalculated as you type: summary of current vs
recommended, post-tax election vs available pay per paycheck, monthly cash flow, and every
paycheck. Press "Save inputs" to keep your numbers in `config.local.json` (git-ignored). Until
you save, the form shows the example numbers. The server listens on 127.0.0.1 only, rejects
requests addressed to any other host name, and sends nothing off your machine.

## Run from the command line

```
cp examples/example_config.json config.json     # config.json is git-ignored
python -m paycalc config.json                   # summary: current vs recommended
python -m paycalc config.json --schedule        # plus contributions on every pay date
python -m paycalc config.json --json            # machine-readable
python -m unittest discover -s tests            # tests (includes the GUI server)
```

## Inputs (config.json)

| Field | Meaning |
|---|---|
| `filing_status` | `single`, `mfj`, `mfs`, `hoh` |
| `state` | Two-letter code (50 states + DC) |
| `age_end_of_year` | Optional. 50+ adds the catch-up limit, 60-63 the super catch-up |
| `pay.base_salary`, `frequency`, `first_pay_date` | `weekly`, `biweekly`, `semimonthly` (15th and month end), `monthly` (month end). `first_pay_date` is any real pay date, required for weekly/biweekly; 27-paycheck years are handled |
| `monthly_expenses` | Optional. Adds a "From savings" column to the monthly cash flow: expenses minus net pay, in months where net pay falls short |
| `pay.raise` | `effective_date` plus `new_salary` or `percent`. Applies to pay dates on or after the effective date |
| `pay.bonuses` | List of bonuses, each with `pay_date`, `amount` or `percent_of_salary` (of the salary in effect that day), optional `name`, and optional `eligible_for_employee_contributions`, `eligible_for_match`, `eligible_for_nonelective`. Any flag left out falls back to the global flag below, so a regular bonus can be excluded while a retention bonus is included. `pay.bonus` (a single object) still works. A bonus on a regular payday is added to that paycheck (one paycheck's take-home covers everything taken from it); otherwise, or with `"separate_paycheck": true`, it is an off-cycle paycheck |
| `deductions.hsa_per_paycheck`, `hsa_coverage` | Payroll HSA per regular paycheck; `hsa_coverage` (`self`/`family`) enables the annual limit check (55+ catch-up via `age_end_of_year`) |
| `deductions.other_pretax_per_paycheck` | Medical/dental/vision premiums, FSA (Section 125): reduce income tax and FICA |
| `deductions.other_posttax_per_paycheck` | Anything else taken after tax (life insurance, dues, ESPP) |
| `contributions.elections` | List of `{effective_date, pretax_percent, posttax_percent}`. Each applies to pay dates on or after its date, so a mid-year change is two entries. A single `pretax_percent`/`posttax_percent` pair (no list) applies all year |
| `contributions.posttax_type` | `after_tax` = non-Roth after-tax money (counts toward the 415(c) total limit, not the 24,500 deferral limit; mega-backdoor style). `roth` (default) = Roth 401(k), shares the deferral limit with pretax |
| `contributions.acknowledge_mega_backdoor` | Default `false`. While `posttax_type` is `after_tax` and this is false, all after-tax figures are hidden and after-tax elections are ignored. Set to `true` only if your plan allows an in-plan Roth conversion or in-service rollover to a Roth IRA |
| `as_of_date` | Optional. Paychecks on or before it keep your actual elections; the recommendation applies to later paychecks only. Leave it out to plan the whole year from the first paycheck |
| `contributions.bonus_eligible_for_employee_contributions` | Default for bonuses that do not set their own: whether your % is taken from the bonus |
| `employer.match_rate_percent`, `match_up_to_percent_of_pay` | e.g. 50 and 6 = 50% of your first 6% |
| `employer.nonelective_percent` | Unmatched employer contribution, % of eligible pay |
| `employer.bonus_eligible_for_match`, `bonus_eligible_for_nonelective` | Whether those are paid on the bonus |
| `what_if` | Optional `{effective_date, pretax_percent, posttax_percent}`: estimate a proposed election change. It replaces any election from that date on and is shown next to Current and Recommended (summary column, tab, notes). Use the first paycheck that will actually change, since payroll often needs about two weeks |
| `withholding.supplemental_rate_percent` | Flat federal withholding on bonus pay (default 22; 37% applies above $1M of bonuses) |
| `withholding.changes` | List of W-4 settings, each from its `effective_date` onward: `multiple_jobs` (step 2 box), `extra_federal_per_paycheck` (step 4(c)), optional `regular_federal_per_paycheck` (use the amount on your stub; leave out to estimate from IRS brackets) |
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
   cover after estimated taxes, HSA, premiums, other deductions and pretax 401(k). If the cap binds, the tool says how far
   short the year ends up. Elections that add to 100% of gross can never be fully deducted.

The monthly cash flow tables show, per calendar month, gross, taxes, HSA/premiums, pretax and
post-tax contributions, and net pay (what lands in the bank). "Cost" columns show how much take-home
each contribution takes: post-tax costs its full amount, pretax costs less because it lowers taxes.

Real-life payroll order: taxes, HSA, premiums and the pretax 401(k) come out first and the
post-tax election takes what it asks for, limited by what is left. An election near 100% therefore
deducts "whatever remains" on a normal paycheck, and the full election on a paycheck with a bonus.
The output has a table per paycheck (elected, available, deducted, left over, and why) so a bonus
month where the whole election went through, with pay left over, is visible at a glance.

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
- Per-paycheck take-home uses modeled withholding: exact Social Security/Medicare, flat supplemental
  federal rate on bonuses, and regular federal withholding estimated from the IRS percentage method
  (checked to the cent against one real MFJ stub with the multiple-jobs box) or taken from
  `regular_federal_per_paycheck`. Imputed income (e.g. group life) and deductions that change per
  paycheck are not modeled, so a stub can differ by a few dollars. State withholding is the year's
  estimated state tax spread over pay.
- "Projected federal refund / owed" compares withholding to this salary's tax only. With a working
  spouse or other income it is not a prediction of your return.
- HSA: CA and NJ do not exclude HSA contributions from state tax; every other state is assumed to
  follow the federal exclusion. Employer HSA contributions are not modeled.
- Earnings and the tax on converting after-tax money to Roth are not modeled.
- 2026 rule requiring catch-up contributions to be Roth for higher earners.
- Tax is a liability estimate, not paycheck withholding.

Verify the state tables against your state's own instructions before relying on them.
