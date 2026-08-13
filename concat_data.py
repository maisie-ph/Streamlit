import re
import time
import polars as pl
import networkx as nx
from collections import defaultdict

# ============================================================
# CONFIG
# ============================================================
G = graph
PARTITION = partition
SUSPICIOUS_ENTITIES = suspicious_entities
HOPS = 3
TRANSACTIONS_PATH = "transactions.parquet"
OUTPUT_PATH = "flagged_transactions.parquet"


# ============================================================
# STEP 1 — entity -> community frame
# ============================================================
def build_entity_community_frame(partition: dict) -> pl.DataFrame:
    return pl.DataFrame({
        "entity": list(partition.keys()),
        "community_index": list(partition.values()),
    }).with_columns(pl.col("community_index").cast(pl.Int32))

#STEP 1.2 entity account to entity name
#===================================================
import polars as pl
from collections import defaultdict

def build_account_to_names(transactions_path: str,
                             orig_acc_col: str, orig_name_col: str,
                             bene_acc_col: str, bene_name_col: str) -> dict:
    lf = pl.scan_parquet(transactions_path)

    orig_pairs = lf.select(
        pl.col(orig_acc_col).alias("account"),
        pl.col(orig_name_col).alias("name")
    )
    bene_pairs = lf.select(
        pl.col(bene_acc_col).alias("account"),
        pl.col(bene_name_col).alias("name")
    )

    all_pairs = (
        pl.concat([orig_pairs, bene_pairs])
        .drop_nulls()
        .unique()  # dedupe identical (account, name) rows before grouping
        .collect()
    )

    account_to_names = defaultdict(set)
    for account, name in zip(all_pairs["account"].to_list(), all_pairs["name"].to_list()):
        account_to_names[account].add(name)

    return dict(account_to_names)



# ============================================================
# STEP 2 — regulator's boundary-safe patterns
# ============================================================
def build_scenario_patterns(keyword_list: list[str]) -> dict[int, re.Pattern]:
    alt = '|'.join(map(re.escape, keyword_list))
    return {
        1: re.compile(rf'^({alt})(?=[^A-Za-z])', re.IGNORECASE),
        2: re.compile(rf'(?<=[^A-Za-z])({alt})(?=[^A-Za-z])', re.IGNORECASE),
        3: re.compile(rf'(?<=[^A-Za-z])({alt})$', re.IGNORECASE),
        4: re.compile(rf'^({alt})$', re.IGNORECASE),
    }

def match_scenarios_with_details(text, patterns):
    if text is None:
        return [], []
    text = str(text)
    matches, scenarios_used = set(), []
    for num, pattern in patterns.items():
        found = pattern.findall(text)
        if found:
            matches.update(found)
            scenarios_used.extend([num] * len(found))
    return list(matches), scenarios_used


# ============================================================
# STEP 3 — coarse filter -> exact match -> confirmed suspicious entities
# ============================================================
def find_confirmed_suspicious_entities(entities_df, suspicious, patterns):
    coarse_pattern = r'(?i)\b(?:' + '|'.join(re.escape(e) for e in suspicious) + r')\b'
    candidates = entities_df.filter(pl.col("entity").str.contains(coarse_pattern))

    hits = []
    for entity in candidates["entity"].to_list():
        matches, _ = match_scenarios_with_details(entity, patterns)
        if matches:
            hits.append(entity)
    return hits


# ============================================================
# STEP 4a — filter communities that contain >=1 confirmed suspect
#           (cheap: operates on ~350 suspects -> partition dict lookups,
#            no scan over 1-2M entities yet)
# ============================================================
def get_flagged_community_ids(partition: dict, confirmed_suspects: list[str]) -> set:
    flagged = set()
    suspects_by_community = defaultdict(list)
    for entity in confirmed_suspects:
        comm = partition.get(entity)
        if comm is not None:
            flagged.add(comm)
            suspects_by_community[comm].append(entity)
    return flagged, suspects_by_community


# ============================================================
# STEP 4b — bucket entities ONLY for the surviving communities
#           (vectorized Polars filter first -> shrinks 1-2M down to
#            just the entities living in flagged communities, THEN bucket)
# ============================================================
def build_filtered_community_entities(entities_df: pl.DataFrame, flagged_community_ids: set) -> dict:
    filtered = entities_df.filter(pl.col("community_index").is_in(flagged_community_ids))

    community_to_entities = defaultdict(list)
    for entity, comm in zip(filtered["entity"].to_list(), filtered["community_index"].to_list()):
        community_to_entities[comm].append(entity)
    return community_to_entities


# ============================================================
# STEP 5 — N-hop neighborhood (undirected), only on flagged communities
# ============================================================
def get_hop_limited_entities(G, suspects_by_community, community_to_entities, hops=3):
    flagged_entities = set()

    for comm_id, suspects in suspects_by_community.items():
        nodes_in_community = community_to_entities.get(comm_id, [])
        if not nodes_in_community:
            continue

        # undirected view of the induced subgraph -> hops go both directions
        subgraph = G.subgraph(nodes_in_community).to_undirected(as_view=True)

        for suspect in suspects:
            if suspect not in subgraph:
                continue
            ego = nx.ego_graph(subgraph, suspect, radius=hops)
            flagged_entities.update(ego.nodes())

    return flagged_entities


# ============================================================
# STEP 6 — filter the 5M-row transaction dataset
# ============================================================
def filter_transactions(hop_entities: set, transactions_path: str, output_path: str):
    hop_df = pl.DataFrame({"entity": list(hop_entities)})
    lf = pl.scan_parquet(transactions_path)

    orig_hits = lf.join(hop_df.lazy(), left_on="originator", right_on="entity", how="semi")
    bene_hits = lf.join(hop_df.lazy(), left_on="beneficiary", right_on="entity", how="semi")

    result = pl.concat([orig_hits, bene_hits]).unique(subset=["message_key"])
    result.collect().write_parquet(output_path)


# ============================================================
# PIPELINE
# ============================================================
def run_screening_pipeline():
    t0 = time.time()

    entities_df = build_entity_community_frame(PARTITION)
    patterns = build_scenario_patterns(SUSPICIOUS_ENTITIES)

    confirmed_suspects = find_confirmed_suspicious_entities(entities_df, SUSPICIOUS_ENTITIES, patterns)
    print(f"[1/5] confirmed suspicious entities: {len(confirmed_suspects):,} ({time.time()-t0:.1f}s)")

    t1 = time.time()
    flagged_community_ids, suspects_by_community = get_flagged_community_ids(PARTITION, confirmed_suspects)
    print(f"[2/5] flagged communities: {len(flagged_community_ids):,} / 50,000 total "
          f"({time.time()-t1:.1f}s)")

    t2 = time.time()
    community_to_entities = build_filtered_community_entities(entities_df, flagged_community_ids)
    n_entities_in_scope = sum(len(v) for v in community_to_entities.values())
    print(f"[3/5] entities bucketed (flagged communities only): {n_entities_in_scope:,} "
          f"({time.time()-t2:.1f}s)")

    t3 = time.time()
    hop_entities = get_hop_limited_entities(G, suspects_by_community, community_to_entities, hops=HOPS)
    print(f"[4/5] {HOPS}-hop neighborhood (undirected): {len(hop_entities):,} entities "
          f"({time.time()-t3:.1f}s)")

    t4 = time.time()
    filter_transactions(hop_entities, TRANSACTIONS_PATH, OUTPUT_PATH)
    print(f"[5/5] transactions filtered and written ({time.time()-t4:.1f}s)")

    print(f"\nTotal pipeline time: {time.time()-t0:.1f}s")
    return hop_entities, flagged_community_ids


hop_entities, flagged_community_ids = run_screening_pipeline()
