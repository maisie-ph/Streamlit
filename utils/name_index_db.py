"""
Build a lightweight name -> acc_number index by scanning only the needed columns
from the parquet file on both originator and beneficiary sides, using DuckDB
for the build and lookups. Saves the index to disk for later use.

Returns:
   duckdb file storing the name -> ID index with community info
"""

import os
import logging
import json
import duckdb
import polars as pl
import streamlit as st

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
DUCKDB_PATH = os.path.join(DATA_PATH, 'ACC_name_index_duck_db')


# ── Schema version check ───────────────────────────────────────────────────────
def _name_index_needs_rebuild():
    """Returns True if the DB is missing or lacks the risk_types column."""
    if not os.path.exists(DUCKDB_PATH):
        return True
    try:
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
        cols = {r[0] for r in con.execute("DESCRIBE name_index").fetchall()}
        con.close()
        return 'risk_types' not in cols
    except Exception:
        return True


# ── Name index build ───────────────────────────────────────────────────────────
def load_name_index(path_data):
    """Builds the DuckDB name index if missing or schema is outdated. Idempotent."""
    if not _name_index_needs_rebuild():
        logger.info("Name index DB found with correct schema, skipping build")
        return

    filepath = os.path.join(path_data, FILE_NAME)
    os.makedirs(os.path.dirname(DUCKDB_PATH), exist_ok=True)

    logger.info("Building account index into DuckDB...")

    orig = pl.scan_parquet(filepath).select([
        pl.col('ORIGINATOR_NAME').str.to_uppercase().alias('name'),
        pl.col('ORIGINATOR_NEW_ACC_NUMBER').alias('account'),
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
        pl.col('BENEFICIARY_NAME').str.to_uppercase().alias('name'),
        pl.col('BENEFICIARY_NEW_ACC_NUMBER').alias('account'),
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

    all_pairs = (
        pl.concat([orig, bene])
        .filter(
            pl.col('entity_id').is_not_null()
            & pl.col('account').is_not_null()
            & pl.col('community_index').is_not_null()
        )
        .unique(subset=['account', 'entity_id', 'community_index'])
        .collect()
    )

    # Build into a temp file, rename atomically at the end — no partial DB on crash
    tmp_path = DUCKDB_PATH + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    logger.info("Building name index with DuckDB...")
    con = duckdb.connect(tmp_path)
    try:
        con.register("all_pairs_df", all_pairs.to_arrow())

        con.execute("""
            CREATE TABLE name_index AS
            WITH multi_comm AS (
                SELECT name,
                       COUNT(DISTINCT community_index) > 1 AS has_multiple_communities
                FROM all_pairs_df
                GROUP BY name
            ),
            community_stats AS (
                SELECT community_index,
                       COUNT(DISTINCT account) AS community_size,
                       COUNT(DISTINCT CASE WHEN is_flagged THEN account END) AS risk_count,
                       BOOL_OR(is_sayari)     AS has_sayari,
                       BOOL_OR(is_scrutiny)   AS has_scrutiny,
                       BOOL_OR(is_pep)        AS has_pep,
                       BOOL_OR(is_wikiran)    AS has_wikiran,
                       BOOL_OR(is_bank_sensi) AS has_bank_sensi,
                       BOOL_OR(is_crypto)     AS has_crypto
                FROM all_pairs_df
                GROUP BY community_index
            )
            SELECT a.name,
                   a.account,
                   a.community_index,
                   m.has_multiple_communities,
                   cs.community_size,
                   COALESCE(cs.risk_count, 0) AS risk_count,
                   COALESCE(CONCAT_WS(',',
                       CASE WHEN cs.has_sayari     THEN 'Sayari'       END,
                       CASE WHEN cs.has_scrutiny   THEN 'FSI'          END,
                       CASE WHEN cs.has_pep        THEN 'PEP'          END,
                       CASE WHEN cs.has_wikiran    THEN 'WIKI'         END,
                       CASE WHEN cs.has_bank_sensi THEN 'SSD/SSZ Bank' END,
                       CASE WHEN cs.has_crypto     THEN 'Crypto'       END
                   ), '') AS risk_types
            FROM all_pairs_df a
            LEFT JOIN multi_comm m USING (name)
            LEFT JOIN community_stats cs USING (community_index)
        """)

        # Indexes for fast lookups
        con.execute("CREATE INDEX idx_name ON name_index(name)")
        con.execute("CREATE INDEX idx_community ON name_index(community_index)")
        con.execute("CREATE INDEX idx_account ON name_index(account)")

        row_count = con.execute("SELECT COUNT(*) FROM name_index").fetchone()[0]
    finally:
        con.close()

    # Atomic swap — readers never see a half-built DB
    os.replace(tmp_path, DUCKDB_PATH)
    logger.info(f"Name index built: {row_count:,} entries saved to DuckDB")

# ── Cached connection ──────────────────────────────────────────────────────────
@st.cache_resource
def get_db_connection():
    """Hold a single read-only DuckDB connection in memory."""
    load_name_index(DATA_PATH)
    return duckdb.connect(DUCKDB_PATH, read_only=True)


# ── Search function ────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Searching matching accounts...")
def search_names(query, name_index=None):
    """Token-based search: returns all rows where name contains every token (AND logic)."""
    con = get_db_connection()
    tokens = query.upper().strip().split()

    if not tokens:
        return []

    conditions = " AND ".join(["name LIKE ?"] * len(tokens))
    params = [f"%{token}%" for token in tokens]

    rows = con.execute(
        f"SELECT name, account, community_index, has_multiple_communities, community_size, risk_count, risk_types "
        f"FROM name_index WHERE {conditions}",
        params
    ).fetchall()

    return [
        {'name': name, 'acc_number': eid, 'community_index': ci,
         'has_multiple_communities': hmc, 'community_size': cs,
         'risk_count': rc, 'risk_types': rt or ''}
        for name, eid, ci, hmc, cs, rc, rt in rows
    ]
