from pathlib import Path
import pandas as pd
import yaml

AUDIT_PATH = Path("/mnt/PDQ_US/audit")
FILES = {
    "strata": "{}_strata_audit_PDQ.csv",
    "combos": "{}_combos_audit_PDQ.csv",
    "representation": "{}_representation_audit_PDQ.csv",
}
FLAGS = ["hs_ctpy", "digital_asset_mess_name", "msb_fcl_hit"]


def load_config(cfg_path):
    with Path(cfg_path).open() as f:
        return yaml.safe_load(f)


def load_audit_data(prefix, audit_path=AUDIT_PATH):
    return {k: pd.read_csv(audit_path / t.format(prefix)) for k, t in FILES.items()}


def filter_undersampled(combos, flag, full_pct=100):
    mask = combos["stratum_flags"].fillna("").str.contains(flag, regex=False)
    return combos[mask & (combos["sample_pct"] != full_pct)]


def audit_flags(combos, flags=FLAGS):
    return {f: filter_undersampled(combos, f).stratum_flags.unique() for f in flags}


if __name__ == "__main__":
    cfg = load_config("US_3_sampling_config.yml")
    data = load_audit_data(f"{cfg['period']}_{cfg['country']}")
    for flag, combos in audit_flags(data["combos"]).items():
        print(f"\n=== {flag} ===")
        print("\n".join(f"  - {c}" for c in combos) or "  fully sampled")
