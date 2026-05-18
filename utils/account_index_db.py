
import duckdb
import os
import polars as pl
import streamlit as st
import logging
import json

# ── Logging config ────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ── Loading config ────────────────────────────────────────────────────────────────
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
config_file_path = os.path.join(project_root, 'config.json')
with open(config_file_path, 'r') as f:
    config = json.load(f)

# ── Configuration ──────────────────────────────────────────────────────────────
DATA_PATH = config["DATA_PATH"]
FILE_NAME = config["FILE_NAME"]
DUCKDB_PATH = os.path.join(DATA_PATH, 'ACC_account_index.duckdb')


# ── Schema version check ───────────────────────────────────────────────────────
def _account_index_needs_rebuild():
    """Returns True if the DB is missing or lacks the risk_types column."""
    if not os.path.exists(DUCKDB_PATH):
        return True
    try:
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
        cols = {r[0] for r in con.execute("DESCRIBE account_index").fetchall()}
        con.close()
        return 'risk_types' not in cols
    except Exception:
        return True


# ── Build the index ────────────────────────────────────────────────────────────
def load_account_index(path_data):
    filepath = os.path.join(path_data, config["FILE_NAME"])

    if not _account_index_needs_rebuild():
        logger.info("Account index DB found with correct schema, skipping build")
        return

    logger.info("Building account index into DuckDB...")

    orig = pl.scan_parquet(filepath).select([
        pl.col('ORIGINATOR_NEW_ACC_NUMBER').alias('account'),
        pl.col('ORIGINATOR_NAME').alias('name'),
        pl.col('ORIGINATOR_ENTITY_ID').alias('entity_id'),
        pl.col('community_index'),
        (
            pl.col('sender_under_scrutiny') |
            pl.col('sender_is_pep') |
            pl.col('originator_in_wikiran') |
            pl.col('sender_bank_is_bank_sensi') |
            (pl.col('sender_in_crypto').is_in(['HIGH', 'MEDIUM', 'LOW']))
        ).alias('is_flagged'),
        pl.col('Orig_in_Sayari').fill_null(False).alias('is_sayari'),
        pl.col('sender_under_scrutiny').fill_null(False).alias('is_scrutiny'),
        pl.col('sender_is_pep').fill_null(False).alias('is_pep'),
        pl.col('originator_in_wikiran').fill_null(False).alias('is_wikiran'),
        pl.col('sender_bank_is_bank_sensi').fill_null(False).alias('is_bank_sensi'),
        pl.col('sender_in_crypto').is_in(['HIGH', 'MEDIUM', 'LOW']).fill_null(False).alias('is_crypto'),
    ])
    bene = pl.scan_parquet(filepath).select([
        pl.col('BENEFICIARY_NEW_ACC_NUMBER').alias('account'),
        pl.col('BENEFICIARY_NAME').alias('name'),
        pl.col('BENEFICIARY_ENTITY_ID').alias('entity_id'),
        pl.col('community_index'),
        (
            pl.col('receiver_under_scrutiny') |
            pl.col('receiver_is_pep') |
            pl.col('beneficiary_in_wikiran') |
            pl.col('receiver_bank_is_bank_sensi') |
            (pl.col('receiver_in_crypto').is_in(['HIGH', 'MEDIUM', 'LOW']))
        ).alias('is_flagged'),
        pl.col('Bene_in_Sayari').fill_null(False).alias('is_sayari'),
        pl.col('receiver_under_scrutiny').fill_null(False).alias('is_scrutiny'),
        pl.col('receiver_is_pep').fill_null(False).alias('is_pep'),
        pl.col('beneficiary_in_wikiran').fill_null(False).alias('is_wikiran'),
        pl.col('receiver_bank_is_bank_sensi').fill_null(False).alias('is_bank_sensi'),
        pl.col('receiver_in_crypto').is_in(['HIGH', 'MEDIUM', 'LOW']).fill_null(False).alias('is_crypto'),
    ])

    triples = (
        pl.concat([orig, bene])
        .filter(
            pl.col('account').is_not_null()
            & pl.col('entity_id').is_not_null()
            & pl.col('community_index').is_not_null()
        )
        .unique(subset=['account', 'entity_id', 'community_index'])
        .collect()
    )

    os.makedirs(os.path.dirname(DUCKDB_PATH), exist_ok=True)
    tmp_path = DUCKDB_PATH + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    con = duckdb.connect(tmp_path)
    try:
        con.register("triples_df", triples.to_arrow())

        # Final table: one row per (account, entity_id, community_index)
        # + has_multiple_communities flag (per account)
        # + community_size (distinct entities per community)
        con.execute("""
            CREATE TABLE account_index AS
            WITH multi_comm AS (
                SELECT account,
                       COUNT(DISTINCT community_index) > 1 AS has_multiple_communities
                FROM triples_df
                GROUP BY account
            ),
            community_sizes AS (
                SELECT community_index,
                       COUNT(DISTINCT account) AS community_size,
                       COUNT(DISTINCT CASE WHEN is_flagged THEN account END) AS flagged_count,
                       BOOL_OR(is_sayari)     AS has_sayari,
                       BOOL_OR(is_scrutiny)   AS has_scrutiny,
                       BOOL_OR(is_pep)        AS has_pep,
                       BOOL_OR(is_wikiran)    AS has_wikiran,
                       BOOL_OR(is_bank_sensi) AS has_bank_sensi,
                       BOOL_OR(is_crypto)     AS has_crypto
                FROM triples_df
                GROUP BY community_index
            )
            SELECT t.account,
                   t.name,
                   t.entity_id,
                   t.community_index,
                   m.has_multiple_communities,
                   cs.community_size,
                   COALESCE(cs.flagged_count, 0) AS flagged_count,
                   COALESCE(CONCAT_WS(',',
                       CASE WHEN cs.has_sayari     THEN 'Sayari'       END,
                       CASE WHEN cs.has_scrutiny   THEN 'FSI'          END,
                       CASE WHEN cs.has_pep        THEN 'PEP'          END,
                       CASE WHEN cs.has_wikiran    THEN 'WIKI'         END,
                       CASE WHEN cs.has_bank_sensi THEN 'SSD/SSZ Bank' END,
                       CASE WHEN cs.has_crypto     THEN 'Crypto'       END
                   ), '') AS risk_types
            FROM triples_df t
            LEFT JOIN multi_comm m USING (account)
            LEFT JOIN community_sizes cs USING (community_index)
        """)

        con.execute("CREATE INDEX idx_account ON account_index(account)")
        con.execute("CREATE INDEX idx_entity_id ON account_index(entity_id)")
        con.execute("CREATE INDEX idx_community ON account_index(community_index)")
        con.execute("CREATE INDEX idx_name ON account_index(name)")

        row_count = con.execute("SELECT COUNT(*) FROM account_index").fetchone()[0]
    finally:
        con.close()

    # Atomic swap
    os.replace(tmp_path, DUCKDB_PATH)
    logger.info(f"Account index built: {row_count:,} entries saved to DuckDB")


# ── Cached connection ──────────────────────────────────────────────────────────
@st.cache_resource
def get_account_db_connection():
    """Hold a single read-only DuckDB connection in memory."""
    load_account_index(DATA_PATH)
    return duckdb.connect(DUCKDB_PATH, read_only=True)


# ── Search function ────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Searching matching accounts...")
def search_accounts(query):
    """Token-based search: returns all rows where account contains every token (AND logic)."""
    con = get_account_db_connection()
    tokens = query.upper().strip().split()

    if not tokens:
        return []

    conditions = " AND ".join(["account LIKE ?"] * len(tokens))
    params = [f"%{token}%" for token in tokens]

    rows = con.execute(
        f"SELECT account, name, entity_id, community_index, has_multiple_communities, community_size, flagged_count, risk_types "
        f"FROM account_index WHERE {conditions}",
        params,
    ).fetchall()

    return [
        {'account': acc, 'name': name, 'entity_id': eid, 'community_index': ci,
         'has_multiple_communities': hmc, 'community_size': cs,
         'flagged_count': fc, 'risk_types': rt or ''}
        for acc, name, eid, ci, hmc, cs, fc, rt in rows
    ]


# ── Exact lookup ───────────────────────────────────────────────────────────────
def lookup_account(account: str) -> list[dict]:
    """Return all (entity_id, community_index, has_multiple_communities, community_size) rows for an account."""
    con = get_account_db_connection()
    rows = con.execute(
        "SELECT entity_id, community_index, has_multiple_communities, community_size "
        "FROM account_index WHERE account = ?",
        (account,),
    ).fetchall()
    return [
        {'entity_id': eid,'name': name, 'community_index': ci,
         'has_multiple_communities': hmc, 'community_size': cs, 'flagged_count': fc}
        for eid, name, ci, hmc, cs, fc in rows
    ]
