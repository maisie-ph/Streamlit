# /// script
# dependencies = [
#   "polars",
#   "networkx",
# ]
# ///
"""
Suspicious entity screening pipeline (Network project)
--------------------------------------------------------
1. Map account numbers <-> all name variants seen in transactions.
2. Screen unique names against the suspicious list using the regulator's
   boundary-safe matching function (coarse Polars pre-filter + exact re
   lookaround match). Exports an audit trail (account_number, entity_name,
   suspicious_name, community_index) for regulatory documentation.
3. Pre-filter communities: keep only communities containing >=1 confirmed
   suspicious account (cheap - only touches confirmed accounts).
4. Bucket entities only for the surviving communities (vectorized filter,
   avoids bucketing all 1-2M entities upfront).
5. Walk N hops (undirected, both directions) from each confirmed suspicious
   account, constrained to its own community subgraph. Single pass tracks
   which suspicious name(s) reached each entity - covers direct AND
   indirect hits, no duplicate traversal.
6. Filter + annotate the 5M-row transaction dataset: every transaction
   within N hops of a confirmed suspect gets a linked_suspicious_names
   column listing every suspicious term whose network reached it.
   Optional target_month / target_year filter.
"""

import re
import time
import pickle
from collections import defaultdict

import polars as pl
import networkx as nx


# ============================================================
# CONFIG
# ============================================================
GRAPH_PATH = "network_graph.pkl"             # pickled nx.Graph (undirected), nodes = account numbers
PARTITION_PATH = "partition.pkl"             # pickled dict {account_number: community_index}
SUSPICIOUS_PATH = "suspicious_entities.txt"  # one suspicious name per line
TRANSACTIONS_PATH = "transactions.parquet"

ORIG_ACC_COL = "orig_account_number"
ORIG_NAME_COL = "orig_name"
BENE_ACC_COL = "bene_account_number"
BENE_NAME_COL = "bene_name"
MESSAGE_KEY_COL = "message_key"
DATE_COL = "value_date"          # set to None if you don't want any date filter available

HOPS = 3
TARGET_MONTH = None              # e.g. 4 for April - leave None to skip date filtering
TARGET_YEAR = None               # e.g. 2026

AUDIT_TRAIL_OUTPUT = "audit_trail.parquet"
ANNOTATED_TRANSACTIONS_OUTPUT = "flagged_transactions_annotated.parquet"


# ============================================================
# LOADERS
# ============================================================
def load_graph_and_partition(graph_path: str, partition_path: str):
    with open(graph_path, "rb") as f:
        G = pickle.load(f)
    with open(partition_path, "rb") as f:
        partition = pickle.load(f)
    return G, partition


def load_suspicious_entities(path: str) -> list[str]:
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


# ============================================================
# STEP 1 — account <-> name mapping (one account, many name variants)
# ============================================================
def build_account_to_names(transactions_path: str,
                            orig_acc_col: str, orig_name_col: str,
                            bene_acc_col: str, bene_name_col: str) -> dict:
    lf = pl.scan_parquet(transactions_path)

    orig_pairs = lf.select(
        pl.col(orig_acc_col).alias("account"),
        pl.col(orig_name_col).alias("name"),
    )
    bene_pairs = lf.select(
        pl.col(bene_acc_col).alias("account"),
        pl.col(bene_name_col).alias("name"),
    )

    all_pairs = (
        pl.concat([orig_pairs, bene_pairs])
        .with_columns(pl.col("account").cast(pl.Utf8).str.strip_chars())
        .filter(
            pl.col("account").is_not_null()
            & (pl.col("account") != "")
            & pl.col("name").is_not_null()
            & (pl.col("name") != "")
        )
        .unique()
        .collect()
    )

    account_to_names = defaultdict(set)
    for account, name in zip(all_pairs["account"].to_list(), all_pairs["name"].to_list()):
        account_to_names[account].add(name)

    return dict(account_to_names)


# ============================================================
# STEP 2 — regulator's boundary-safe matching patterns (exact logic)
# ============================================================
def build_scenario_patterns(keyword_list: list[str]) -> dict[int, re.Pattern]:
    alt = "|".join(map(re.escape, keyword_list))
    return {
        1: re.compile(rf"^({alt})(?=[^A-Za-z])", re.IGNORECASE),
        2: re.compile(rf"(?<=[^A-Za-z])({alt})(?=[^A-Za-z])", re.IGNORECASE),
        3: re.compile(rf"(?<=[^A-Za-z])({alt})$", re.IGNORECASE),
        4: re.compile(rf"^({alt})$", re.IGNORECASE),
    }


def match_scenarios_with_details(text, patterns: dict[int, re.Pattern]):
    if text is None:
        return [], []
    text = str(text)
    matches = set()
    scenarios_used = []
    for scenario_num, pattern in patterns.items():
        found = pattern.findall(text)
        if found:
            matches.update(found)
            scenarios_used.extend([scenario_num] * len(found))
    return list(matches), scenarios_used


# ============================================================
# STEP 3 — coarse filter -> exact match -> confirmed accounts + audit export
# ============================================================
def find_confirmed_suspicious_accounts(account_to_names: dict,
                                        suspicious: list[str],
                                        patterns: dict,
                                        partition: dict = None,
                                        export_path: str = None) -> set:
    """
    account_to_names: {account_number: {name_variant_1, name_variant_2, ...}}
    suspicious: watchlist of suspicious names to screen against
    patterns: output of build_scenario_patterns(suspicious)
    partition: {account_number: community_index} - if provided, community_index
               is attached to each export row
    export_path: if provided (.csv or .parquet), writes the audit trail:
                 account_number, entity_name, suspicious_name[, community_index]

    entity_name  = the real name found in the transaction data (haystack)
    suspicious_name = the watchlist term that matched inside it (needle)

    Returns: set of confirmed suspicious account numbers
    """
    accounts, names = [], []
    for account, name_set in account_to_names.items():
        for name in name_set:
            accounts.append(account)
            names.append(name)

    flat_df = pl.DataFrame({"account": accounts, "name": names})

    # Stage 1: coarse filter - fast vectorized Rust regex, superset of true matches
    coarse_pattern = r"(?i)\b(?:" + "|".join(re.escape(e) for e in suspicious) + r")\b"
    candidates = flat_df.filter(pl.col("name").str.contains(coarse_pattern))

    # Stage 2: exact regulator match (boundary-safe lookaround) on candidates only
    confirmed_accounts = set()
    export_rows = []

    for account, name in zip(candidates["account"].to_list(), candidates["name"].to_list()):
        matches, _ = match_scenarios_with_details(name, patterns)
        if matches:
            confirmed_accounts.add(account)
            for term in matches:
                row = {
                    "account_number": account,
                    "entity_name": name,
                    "suspicious_name": term,
                }
                if partition is not None:
                    row["community_index"] = partition.get(account)
                export_rows.append(row)

    if export_path and export_rows:
        export_df = pl.DataFrame(export_rows)
        if export_path.endswith(".csv"):
            export_df.write_csv(export_path)
        elif export_path.endswith(".parquet"):
            export_df.write_parquet(export_path)
        else:
            raise ValueError(f"Unsupported export extension for {export_path} (use .csv or .parquet)")

    return confirmed_accounts


def build_account_to_suspicious_names(account_to_names: dict,
                                       suspicious: list[str],
                                       patterns: dict) -> dict:
    """
    {account_number: {suspicious_name1, suspicious_name2, ...}} for every
    confirmed hit - used to carry provenance into the hop traversal.
    Cheap to rebuild from the same matching pass; if you already exported
    an audit trail, you can instead read it back and invert it (see
    account_to_suspicious_names_from_audit below) to avoid re-matching.
    """
    accounts, names = [], []
    for account, name_set in account_to_names.items():
        for name in name_set:
            accounts.append(account)
            names.append(name)

    flat_df = pl.DataFrame({"account": accounts, "name": names})
    coarse_pattern = r"(?i)\b(?:" + "|".join(re.escape(e) for e in suspicious) + r")\b"
    candidates = flat_df.filter(pl.col("name").str.contains(coarse_pattern))

    mapping = defaultdict(set)
    for account, name in zip(candidates["account"].to_list(), candidates["name"].to_list()):
        matches, _ = match_scenarios_with_details(name, patterns)
        if matches:
            mapping[account].update(matches)

    return dict(mapping)


def account_to_suspicious_names_from_audit(audit_trail: pl.DataFrame) -> dict:
    """Inverts an already-computed audit trail DataFrame instead of re-matching."""
    mapping = defaultdict(set)
    for acc, term in zip(audit_trail["account_number"].to_list(), audit_trail["suspicious_name"].to_list()):
        mapping[acc].add(term)
    return dict(mapping)


# ============================================================
# STEP 4 — community pre-filter (cheap: only touches confirmed accounts)
# ============================================================
def get_flagged_community_ids(partition: dict, confirmed_accounts: set):
    flagged = set()
    suspects_by_community = defaultdict(list)
    for account in confirmed_accounts:
        comm = partition.get(account)
        if comm is not None:
            flagged.add(comm)
            suspects_by_community[comm].append(account)
    return flagged, suspects_by_community


def build_entity_community_frame(partition: dict) -> pl.DataFrame:
    return pl.DataFrame({
        "account": list(partition.keys()),
        "community_index": list(partition.values()),
    }).with_columns(pl.col("community_index").cast(pl.Int32))


def build_filtered_community_entities(entities_df: pl.DataFrame, flagged_community_ids: set) -> dict:
    filtered = entities_df.filter(pl.col("community_index").is_in(flagged_community_ids))
    community_to_entities = defaultdict(list)
    for account, comm in zip(filtered["account"].to_list(), filtered["community_index"].to_list()):
        community_to_entities[comm].append(account)
    return community_to_entities


# ============================================================
# STEP 5 — undirected N-hop traversal, single pass, WITH provenance
# ============================================================
def get_hop_limited_entities(G, suspects_by_community, community_to_entities,
                              account_to_suspicious_names: dict, hops: int = 3) -> dict:
    """
    Walks N hops from each confirmed suspicious account, constrained to its
    own community subgraph (undirected -> direction of payment doesn't
    matter). Single traversal pass: every reached account is tagged with
    every suspicious name whose walk reached it, covering both direct
    (the account itself matched) and indirect (reached via the graph) hits.

    Returns: {account: {suspicious_name1, suspicious_name2, ...}}
    The plain set of reached accounts is simply reached_entity_to_names.keys().
    """
    reached_entity_to_names = defaultdict(set)

    for comm_id, suspects in suspects_by_community.items():
        nodes_in_community = community_to_entities.get(comm_id, [])
        if not nodes_in_community:
            continue

        subgraph = G.subgraph(nodes_in_community)  # nx.Graph -> already undirected

        for suspect in suspects:
            if suspect not in subgraph:
                continue
            ego = nx.ego_graph(subgraph, suspect, radius=hops)
            names_for_suspect = account_to_suspicious_names.get(suspect, set())

            for reached_account in ego.nodes():
                reached_entity_to_names[reached_account].update(names_for_suspect)

    return reached_entity_to_names


# ============================================================
# STEP 6 — filter + annotate the 5M-row transaction dataset
# ============================================================
def filter_and_annotate_transactions(reached_entity_to_names: dict,
                                      transactions_path: str,
                                      output_path: str,
                                      orig_acc_col: str,
                                      bene_acc_col: str,
                                      message_key_col: str,
                                      date_col: str = None,
                                      target_month: int = None,
                                      target_year: int = None):
    """
    Keeps every transaction where orig OR bene is within N hops of a
    confirmed suspicious account, and adds linked_suspicious_names listing
    every suspicious term whose network reached that transaction.
    Optional target_month/target_year restricts to that calendar month
    (date_col must be a Date/Datetime dtype).
    """
    hop_accounts = list(reached_entity_to_names.keys())
    hop_df = pl.DataFrame({
        "account": hop_accounts,
        "linked_names_orig": [list(reached_entity_to_names[a]) for a in hop_accounts],
    })
    hop_df_bene = hop_df.rename({"linked_names_orig": "linked_names_bene"})

    lf = pl.scan_parquet(transactions_path)

    if date_col and target_month and target_year:
        lf = lf.filter(
            (pl.col(date_col).dt.month() == target_month)
            & (pl.col(date_col).dt.year() == target_year)
        )

    with_orig = lf.join(hop_df.lazy(), left_on=orig_acc_col, right_on="account", how="left")
    with_both = with_orig.join(hop_df_bene.lazy(), left_on=bene_acc_col, right_on="account", how="left")

    result = (
        with_both.filter(
            pl.col("linked_names_orig").is_not_null() | pl.col("linked_names_bene").is_not_null()
        )
        .with_columns(
            pl.concat_list([
                pl.col("linked_names_orig").fill_null([]),
                pl.col("linked_names_bene").fill_null([]),
            ]).list.unique().alias("linked_suspicious_names")
        )
        .drop(["linked_names_orig", "linked_names_bene"])
        .unique(subset=[message_key_col])
    )

    result.collect().write_parquet(output_path)


# ============================================================
# PIPELINE
# ============================================================
def run_screening_pipeline(G, partition, suspicious_entities: list[str],
                            transactions_path: str,
                            orig_acc_col: str, orig_name_col: str,
                            bene_acc_col: str, bene_name_col: str,
                            message_key_col: str,
                            hops: int,
                            audit_trail_output: str,
                            annotated_transactions_output: str,
                            date_col: str = None,
                            target_month: int = None,
                            target_year: int = None):
    t0 = time.time()

    account_to_names = build_account_to_names(
        transactions_path, orig_acc_col, orig_name_col, bene_acc_col, bene_name_col
    )
    print(f"[1/6] account -> names mapping: {len(account_to_names):,} accounts ({time.time()-t0:.1f}s)")

    patterns = build_scenario_patterns(suspicious_entities)

    t1 = time.time()
    confirmed_accounts = find_confirmed_suspicious_accounts(
        account_to_names, suspicious_entities, patterns,
        partition=partition, export_path=audit_trail_output
    )
    print(f"[2/6] confirmed suspicious accounts: {len(confirmed_accounts):,} "
          f"-> {audit_trail_output} ({time.time()-t1:.1f}s)")

    account_to_suspicious_names = build_account_to_suspicious_names(
        account_to_names, suspicious_entities, patterns
    )

    t2 = time.time()
    flagged_community_ids, suspects_by_community = get_flagged_community_ids(partition, confirmed_accounts)
    print(f"[3/6] flagged communities: {len(flagged_community_ids):,} ({time.time()-t2:.1f}s)")

    t3 = time.time()
    entities_df = build_entity_community_frame(partition)
    community_to_entities = build_filtered_community_entities(entities_df, flagged_community_ids)
    n_entities_in_scope = sum(len(v) for v in community_to_entities.values())
    print(f"[4/6] entities bucketed (flagged communities only): {n_entities_in_scope:,} "
          f"({time.time()-t3:.1f}s)")

    t4 = time.time()
    reached_entity_to_names = get_hop_limited_entities(
        G, suspects_by_community, community_to_entities, account_to_suspicious_names, hops=hops
    )
    print(f"[5/6] {hops}-hop neighborhood: {len(reached_entity_to_names):,} entities reached "
          f"({time.time()-t4:.1f}s)")

    t5 = time.time()
    filter_and_annotate_transactions(
        reached_entity_to_names, transactions_path, annotated_transactions_output,
        orig_acc_col, bene_acc_col, message_key_col,
        date_col=date_col, target_month=target_month, target_year=target_year
    )
    print(f"[6/6] transactions filtered & annotated -> {annotated_transactions_output} "
          f"({time.time()-t5:.1f}s)")

    print(f"\nTotal pipeline time: {time.time()-t0:.1f}s")
    return confirmed_accounts, reached_entity_to_names


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    G, PARTITION = load_graph_and_partition(GRAPH_PATH, PARTITION_PATH)
    SUSPICIOUS_ENTITIES = load_suspicious_entities(SUSPICIOUS_PATH)

    confirmed_accounts, reached_entity_to_names = run_screening_pipeline(
        G=G,
        partition=PARTITION,
        suspicious_entities=SUSPICIOUS_ENTITIES,
        transactions_path=TRANSACTIONS_PATH,
        orig_acc_col=ORIG_ACC_COL,
        orig_name_col=ORIG_NAME_COL,
        bene_acc_col=BENE_ACC_COL,
        bene_name_col=BENE_NAME_COL,
        message_key_col=MESSAGE_KEY_COL,
        hops=HOPS,
        audit_trail_output=AUDIT_TRAIL_OUTPUT,
        annotated_transactions_output=ANNOTATED_TRANSACTIONS_OUTPUT,
        date_col=DATE_COL,
        target_month=TARGET_MONTH,
        target_year=TARGET_YEAR,
    )
