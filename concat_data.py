#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Sample adjustment loop (stage 3).

Runs the representation check of US_5_representation_check.py and, as long as
at least one flag fails, tops-up the sample with population pairs and checks
again - until every flag passes.

Each round:
  1. run the representation check on the current sample;
  2. stop if every flag passes;
  3. for every failing flag, find the minimum number of extra pairs carrying
     that flag that would make it pass (binary search);
  4. draw those pairs from the population pairs that are not yet sampled,
     preferring pairs that carry several failing flags at once;
  5. add them to the sample and go back to 1 (adding rows changes the sample
     size, so a flag that was passing can fail again).

The loop also stops (and says why) when the maximum number of rounds or the
maximum number of added pairs is reached, or when no more pairs can be added
for the failing flags.

Usage
-----
python US_6_adjust_sample.py \
    --population pairs.csv \
    --sample sampled_pairs.csv \
    --config sampling_config.yml \
    [--max-rounds 20] \
    [--max-add 1000] \
    [--seed 911]

US_5_representation_check.py must be in the same folder as this file.

Outputs (in the `audit` folder used by US_5_representation_check.py)
-------
adjusted_sampled_pairs.csv          original sample + added pairs
                                    (column `adjustment_round`: 0 = original)
sample_adjustment_log.csv           one row per added pair
adjustment_history.csv              one row per round (failing flags, size)
representation_audit_PDQ_round<k>.csv   audit after k top-up rounds
representation_audit_PDQ.csv        final audit
"""

import argparse
import importlib
import random
import sys
import traceback
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
rep = importlib.import_module("US_5_representation_check")


# ---------------------------------------------------------------------
# 1. Pass / fail rule (same rule as the main loop of US_5)
# ---------------------------------------------------------------------
def flag_passes(
    pop_cnt: int,
    samp_cnt: int,
    N_pop: int,
    N_samp: int,
    alpha: float,
    confidence: float,
    small_sample: int,
) -> bool:
    """A flag passes if the one-sided test rejects H0 AND the lower bound of the CI is above p_pop."""
    pop_prop = pop_cnt / N_pop if N_pop else 0.0
    samp_prop = samp_cnt / N_samp if N_samp else 0.0

    if pop_cnt < small_sample:
        test_res = rep.exact_binom_test(pop_prop, samp_cnt, N_samp, pop_cnt, alpha=alpha, alternative="greater")
    else:
        test_res = rep.one_sided_z_test(pop_prop, samp_prop, N_samp, pop_cnt, alpha=alpha)

    if pop_cnt > small_sample:
        ci_res = rep.wilson_ci(samp_prop, N_samp, confidence=confidence)
    else:
        ci_res = rep.jeffreys_ci(samp_prop, N_samp, confidence=confidence)

    lb_res = rep.lower_bound_check(pop_prop, ci_res, pop_cnt, samp_cnt)
    return bool(test_res["reject_H0"]) and bool(lb_res["lower_exceeds_pop"])


# ---------------------------------------------------------------------
# 2. One top-up round
# ---------------------------------------------------------------------
def top_up_round(
    pop_df: pd.DataFrame,
    samp_df: pd.DataFrame,
    failing: list,
    pop_masks: dict,
    pop_cnt: dict,
    N_pop: int,
    alpha: float,
    confidence: float,
    small_sample: int,
    rng: random.Random,
    budget: Optional[int],
) -> pd.DataFrame:
    """Return the population rows to add to the sample so that the failing flags can pass."""
    N_samp = len(samp_df)
    samp_cnt = {f: int(rep.flag_mask(samp_df["canonical"], f).sum()) for f in failing}

    # Population pairs that are not in the sample yet
    pool = pop_df[~pop_df["Pair_ID"].isin(set(samp_df["Pair_ID"]))]
    if pool.empty:
        return pool

    fail_masks = {f: pop_masks[f].loc[pool.index] for f in failing}
    # How many failing flags does each candidate pair carry?
    score = sum(m.astype(int) for m in fail_masks.values())

    chosen = set()
    for f in sorted(failing, key=lambda x: pop_cnt[x]):            # rarest flag first
        candidates = list(pool.index[fail_masks[f]])
        if not candidates:
            continue

        # Minimum number k of extra pairs (all carrying f) that makes f pass
        lo, hi = 0, len(candidates)
        while lo < hi:
            mid = (lo + hi) // 2
            if flag_passes(pop_cnt[f], samp_cnt[f] + mid, N_pop, N_samp + mid,
                           alpha, confidence, small_sample):
                hi = mid
            else:
                lo = mid + 1
        k = lo

        # Pairs already chosen for another flag in this round also count
        already = int(fail_masks[f].loc[list(chosen)].sum()) if chosen else 0
        need = max(0, k - already)

        remaining = [i for i in candidates if i not in chosen]
        rng.shuffle(remaining)                                      # random tie-break
        remaining.sort(key=lambda i: -score.loc[i])                 # multi-flag pairs first
        chosen.update(remaining[:need])

    if budget is not None and len(chosen) > budget:
        chosen = set(sorted(chosen, key=lambda i: -score.loc[i])[:budget])

    return pop_df.loc[sorted(chosen)].copy()


# ---------------------------------------------------------------------
# 3. Check -> adjust -> check ... until every flag passes
# ---------------------------------------------------------------------
def run_until_pass(
    population_path: Path,
    sample_path: Path,
    config_path: Path,
    seed: int = 911,
    max_rounds: int = 20,
    max_add: Optional[int] = None,
) -> Tuple[pd.DataFrame, bool]:
    cfg = rep.load_config(config_path)
    alpha = cfg.get("alpha", 0.05)
    small_sample = cfg.get("small_sample", 30)
    confidence = cfg["ci"]["confidence"]

    rng = random.Random(seed)
    audit_dir = rep._ensure_audit_dir()
    adjusted_path = audit_dir / "adjusted_sampled_pairs.csv"

    # ----- load data -----------------------------------------------------
    pop_df = pd.read_csv(population_path, dtype=str, sep=";")
    pop_df = pop_df[pop_df["list_flags"].isna() == False].copy()
    pop_df["canonical"] = pop_df["list_flags"].apply(rep.canonical_flags)

    samp_df = pd.read_csv(sample_path, dtype=str, sep=";")
    if "canonical" not in samp_df.columns:
        samp_df["canonical"] = samp_df["list_flags"].apply(rep.canonical_flags)
    if "adjustment_round" not in samp_df.columns:
        samp_df["adjustment_round"] = 0

    N_pop = len(pop_df)
    original_size = len(samp_df)
    all_flags = rep.extract_all_flags(pop_df, column="canonical")
    pop_masks = {f: rep.flag_mask(pop_df["canonical"], f) for f in all_flags}
    pop_cnt = {f: int(m.sum()) for f, m in pop_masks.items()}

    current_path = Path(sample_path)
    history, log_frames = [], []
    total_added = 0
    converged = False
    stop_reason = ""
    audit_df = pd.DataFrame()

    # ----- loop: check, then top-up --------------------------------------
    for rnd in range(0, max_rounds + 1):
        print(f"\n########## Round {rnd}: checking the sample ({len(samp_df)} pairs) ##########")
        audit_df = rep.run_representation_check(
            population_path=population_path,
            sample_path=current_path,
            config_path=config_path,
        )
        audit_df.to_csv(audit_dir / f"representation_audit_PDQ_round{rnd}.csv", index=False)

        failing = audit_df.loc[~audit_df["passed"].astype(bool), "flag"].tolist()
        history.append({
            "round": rnd,
            "sample_size": len(samp_df),
            "pairs_added_so_far": total_added,
            "n_failing_flags": len(failing),
            "failing_flags": ",".join(failing),
        })

        if not failing:
            converged = True
            break
        if rnd == max_rounds:
            stop_reason = f"maximum number of rounds reached ({max_rounds})"
            break
        budget = None if max_add is None else max_add - total_added
        if budget is not None and budget <= 0:
            stop_reason = f"maximum number of added pairs reached ({max_add})"
            break

        new_rows = top_up_round(
            pop_df, samp_df, failing, pop_masks, pop_cnt, N_pop,
            alpha, confidence, small_sample, rng, budget,
        )
        if new_rows.empty:
            stop_reason = (
                "no more pairs can be added for the failing flags "
                "(all their population pairs are already in the sample)"
            )
            break

        new_rows["adjustment_round"] = rnd + 1
        samp_df = pd.concat([samp_df, new_rows], ignore_index=True)
        total_added += len(new_rows)
        samp_df.to_csv(adjusted_path, sep=";", index=False)
        current_path = adjusted_path

        log_frames.append(pd.DataFrame({
            "round": rnd + 1,
            "Pair_ID": new_rows["Pair_ID"].values,
            "flags": new_rows["canonical"].values,
            "failing_flags_covered": [
                ",".join(f for f in failing if pop_masks[f].loc[i]) for i in new_rows.index
            ],
        }))
        print(f"\n-> {len(failing)} flag(s) failed ({', '.join(failing)}): "
              f"added {len(new_rows)} pair(s) from the population.")

    # ----- outputs -------------------------------------------------------
    log_df = (
        pd.concat(log_frames, ignore_index=True)
        if log_frames
        else pd.DataFrame(columns=["round", "Pair_ID", "flags", "failing_flags_covered"])
    )
    log_df.to_csv(audit_dir / "sample_adjustment_log.csv", sep=";", index=False)
    pd.DataFrame(history).to_csv(audit_dir / "adjustment_history.csv", index=False)

    print("\n" + "=" * 60)
    if converged:
        print(f"✅ All {len(all_flags)} flags pass.")
    else:
        failing = history[-1]["failing_flags"]
        print(f"⚠️ Stopped before all flags passed: {stop_reason}.")
        print(f"   Still failing: {failing}")
    print(f"   Sample size       : {original_size} -> {len(samp_df)} ({total_added} pair(s) added)")
    print(f"   Rounds            : {len(history) - 1} top-up round(s)")
    if total_added:
        print(f"   Adjusted sample   : {adjusted_path}")
        print(f"   Adjustment log    : {audit_dir / 'sample_adjustment_log.csv'}")
    print(f"   History           : {audit_dir / 'adjustment_history.csv'}")
    print("=" * 60 + "\n")

    return audit_df, converged


# ---------------------------------------------------------------------
# 4. CLI entry point
# ---------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Top-up the sample and re-check until every flag passes the representation tests."
    )
    parser.add_argument(
        "--population",
        default="pairs_for_sampling.csv",
        help="CSV file with the full population (pairs.csv).",
    )
    parser.add_argument(
        "--sample",
        default="refactored_sampled_pairs_test1708.csv",
        help="CSV file with the sample produced by the first pipeline (sampled_pairs.csv).",
    )
    parser.add_argument(
        "--config",
        default="US_3_sampling_config.yml",
        help="YAML configuration file (same as used for sampling).",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=20,
        help="Maximum number of top-up rounds (default=20).",
    )
    parser.add_argument(
        "--max-add",
        type=int,
        default=None,
        help="Maximum total number of pairs that may be added (default=no limit).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=911,
        help="Random seed used to draw the additional pairs (default=911).",
    )
    args = parser.parse_args()

    try:
        _, converged = run_until_pass(
            population_path=Path(args.population),
            sample_path=Path(args.sample),
            config_path=Path(args.config),
            seed=args.seed,
            max_rounds=args.max_rounds,
            max_add=args.max_add,
        )
    except Exception:
        print("\n❌ An error occurred while adjusting the sample:", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

    sys.exit(0 if converged else 2)


if __name__ == "__main__":
    main()
