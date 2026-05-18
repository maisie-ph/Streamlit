import streamlit as st
import pandas as pd
import gc
import pyarrow.parquet as pq
import logging
import polars as pl
import json
import os
from utils.optimize_df_memory import optimize_dataframe_memory

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

# ── Threshold for full graph display ────────────────────────────────────────────────────────────────
MAX_ENTITIES_FULL_GRAPH = 1000



# ── USED COLUMNS ────────────────────────────────────────────────────────────────
COLUMNS_TO_USE = [
    'ORIGINATOR_NAME',
    'ORIGINATOR_ACC_NUMBER',
    'ORIGINATOR_NEW_ACC_NUMBER',
    'ORIGINATOR_ENTITY_ID',
    'BENEFICIARY_NAME',
    'BENEFICIARY_ACC_NUMBER',
    'BENEFICIARY_NEW_ACC_NUMBER',
    'BENEFICIARY_ENTITY_ID',
    'BASE_CURR_AMOUNT',
    'EXECUTION_GLOBAL_DATE_TIME',
    'sender_under_scrutiny',
    'receiver_under_scrutiny',
    'sender_is_pep',
    'receiver_is_pep',
    'originator_in_wikiran',
    'beneficiary_in_wikiran',
    'sender_in_crypto',
    'receiver_in_crypto',
    'sender_bank_is_bank_sensi',
    'receiver_bank_is_bank_sensi',
    'Orig_in_Sayari',
    'Bene_in_Sayari',
    'community_index',
    'BIC_ORIGINATOR',
    'BIC_BENEFICIARY'

]



# ── Function to count distinct entities within one community ────────────────────────────────────────────────────────────────
def count_community_entities(path_data, community_component):
    """
    Count distinct entities in a community using a lazy Polars scan.
    does not load the full dataset into memory.

    Returns:
        int: number of distinct entities (originators + beneficiaries)
    """
    filepath = os.path.join(path_data, config["FILE_NAME"])
    community_component = int(float(community_component))

    logger.info(f"Counting distinct entities for community {community_component}...")

    result = (
        pl.scan_parquet(filepath)
        .filter(pl.col('community_index') == community_component)
        .select([
            pl.col('ORIGINATOR_NEW_ACC_NUMBER'),
            pl.col('BENEFICIARY_NEW_ACC_NUMBER')
        ])
        .collect()
    )

    # Union of originators and beneficiaries, drop nulls, count distinct
    originators = result['ORIGINATOR_NEW_ACC_NUMBER'].drop_nulls()
    beneficiaries = result['BENEFICIARY_NEW_ACC_NUMBER'].drop_nulls()
    distinct_count = len(pl.Series(pl.concat([originators, beneficiaries])).unique())

    logger.info(f"Community {community_component} has {distinct_count} distinct entities")
    return distinct_count



# ── Function that returns a Boolean value deciding whether a community is too large for full display in graph or not ────────────────────────────────────
def is_community_too_large(path_data, community_component):
    """
    Returns True if the community (or combined communities) exceeds
    MAX_ENTITIES_FULL_GRAPH distinct entities.
    Handles both a single community ID and a list of community IDs.
    """
    if isinstance(community_component, list):
        total_count = sum(
            count_community_entities(path_data, c)
            for c in community_component
        )
        return total_count > MAX_ENTITIES_FULL_GRAPH, total_count
    else:
        count = count_community_entities(path_data, community_component)
        return count > MAX_ENTITIES_FULL_GRAPH, count


# ── Function to load data relative to one community ────────────────────────────────────────────────────────────────
def load_community_data(community_component, path_data=None):
    """
    Loads data from Parquet file with optimized memory usage and filters by community component.
    """
    try:
        filename = config["FILE_NAME"]
        filepath = os.path.join(path_data, filename)

        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File {filename} doesn't exist in {path_data}")

        if community_component is None:
            raise ValueError("community_component must be provided")

        community_component = int(float(community_component))
        logger.info(f"Loading transactions for community component: '{community_component}'")

        parquet_file = pq.ParquetFile(filepath)
        available_cols = [col for col in COLUMNS_TO_USE if col in parquet_file.schema.names]

        logger.info(f"Columns to use: {available_cols}")

        filters = [('community_index', '=', community_component)]

        table = pq.read_table(
            filepath,
            columns=available_cols,
            filters=filters,
            use_threads=True
        )

        community_df = table.to_pandas(
            self_destruct=True,
            types_mapper={
                'ORIGINATOR_NEW_ACC_NUMBER': pd.StringDtype(),
                'BENEFICIARY_NEW_ACC_NUMBER': pd.StringDtype(),
                'community_index': pd.Int32Dtype()
            }.get
        )

        del table
        gc.collect()

        if community_df.empty:
            logger.info(f"No data found for community component: {community_component}")
            sample_table = pq.read_table(filepath, columns=['community_index'])
            unique_vals = sample_table.to_pandas()['community_index'].unique()[:20]
            logger.info(f"Sample community_index values available: {unique_vals}")
            del sample_table
            gc.collect()
            return pd.DataFrame()

        community_df = optimize_dataframe_memory(community_df)

        logger.info(f"Successfully loaded {len(community_df):,} transactions")
        logger.info(f"Memory usage: {community_df.memory_usage(deep=True).sum() / 1024**2:.2f} MB")

        return community_df

    except Exception as e:
        logger.info(f"Error loading data: {str(e)}")
        raise
    finally:
        gc.collect()


# ── Function that finds the first entity ID that appears for one Entity Name ────────────────────────────────────────────────────────────────
def _resolve_name_to_id(filepath, account_name):
    """
    Resolve an Entity Name to its NEW_ACC_NUMBER using a lazy scan.
    """
    name_upper = account_name.strip().upper()

    result = (
        pl.scan_parquet(filepath)
        .select(['ORIGINATOR_NAME', 'ORIGINATOR_NEW_ACC_NUMBER'])
        .filter(pl.col('ORIGINATOR_NAME').str.to_uppercase() == name_upper)
        .select('ORIGINATOR_NEW_ACC_NUMBER')
        .first()
        .collect()
    )

    if result.is_empty():
        # Try beneficiary side
        result = (
            pl.scan_parquet(filepath)
            .select(['BENEFICIARY_NAME', 'BENEFICIARY_NEW_ACC_NUMBER'])
            .filter(pl.col('BENEFICIARY_NAME').str.to_uppercase() == name_upper)
            .select('BENEFICIARY_NEW_ACC_NUMBER')
            .first()
            .collect()
        )

    if result.is_empty():
        raise ValueError(f"No account found for name: {account_name}")

    return str(result[0, 0])


# ── Function to find a community index for a given Entity ID  ────────────────────────────────────────────────────────────────
def find_community_for_account(path_data, account_id=None):
    """
    Find the community index for a given Entity ID.
    Returns the community index.
    """
    if account_id is None:
        logger.error("account_id must be provided")

    filename = config["FILE_NAME"]
    filepath = os.path.join(path_data, filename)

    if account_id:
        result = (
            pl.scan_parquet(filepath)
            .select(['ORIGINATOR_NEW_ACC_NUMBER', 'BENEFICIARY_NEW_ACC_NUMBER', 'community_index'])
            .filter(
                (pl.col('ORIGINATOR_NEW_ACC_NUMBER') == account_id) |
                (pl.col('BENEFICIARY_NEW_ACC_NUMBER') == account_id)
            )
            .select('community_index')
            .first()
            .collect()
        )

    if result.is_empty():
        raise ValueError(f"No Community found for {'account_id:' + account_id}")

    return int(result['community_index'][0])


# ── Risk type labels (order determines display order) ──────────────────────────
RISK_TYPE_OPTIONS = ['Sayari', 'FSI', 'PEP', 'WIKI', 'SSD/SSZ Bank', 'Crypto']


def _risk_types_from_row(row: dict) -> str:
    """Build comma-separated risk type string from per-community boolean flags."""
    parts = []
    if row.get('has_sayari'):     parts.append('Sayari')
    if row.get('has_scrutiny'):   parts.append('FSI')
    if row.get('has_pep'):        parts.append('PEP')
    if row.get('has_wikiran'):    parts.append('WIKI')
    if row.get('has_bank_sensi'): parts.append('SSD/SSZ Bank')
    if row.get('has_crypto'):     parts.append('Crypto')
    return ','.join(parts)


# ── Function to find ALL community indices for a given BIC ────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def find_communities_for_bic(path_data, bic):
    """
    Find ALL community indices where a given BIC appears,
    either as BIC_ORIGINATOR or BIC_BENEFICIARY.
    Returns a list of dicts with BIC, community_index, transaction_count and
    risk_types (comma-separated labels of risk categories present in the community),
    sorted by transaction_count descending.
    """
    if not bic:
        raise ValueError("BIC must be provided")

    filename = config["FILE_NAME"]
    filepath = os.path.join(path_data, filename)
    bic_upper = bic.strip().upper()

    result = (
        pl.scan_parquet(filepath)
        .select([
            'BIC_ORIGINATOR', 'BIC_BENEFICIARY', 'community_index',
            'Orig_in_Sayari', 'Bene_in_Sayari',
            'sender_under_scrutiny', 'receiver_under_scrutiny',
            'sender_is_pep', 'receiver_is_pep',
            'originator_in_wikiran', 'beneficiary_in_wikiran',
            'sender_bank_is_bank_sensi', 'receiver_bank_is_bank_sensi',
            'sender_in_crypto', 'receiver_in_crypto',
        ])
        .filter(
            (pl.col('BIC_ORIGINATOR').str.to_uppercase() == bic_upper) |
            (pl.col('BIC_BENEFICIARY').str.to_uppercase() == bic_upper)
        )
        .group_by('community_index')
        .agg([
            pl.count().alias('transaction_count'),
            (pl.col('Orig_in_Sayari').fill_null(False) | pl.col('Bene_in_Sayari').fill_null(False)).any().alias('has_sayari'),
            (pl.col('sender_under_scrutiny').fill_null(False) | pl.col('receiver_under_scrutiny').fill_null(False)).any().alias('has_scrutiny'),
            (pl.col('sender_is_pep').fill_null(False) | pl.col('receiver_is_pep').fill_null(False)).any().alias('has_pep'),
            (pl.col('originator_in_wikiran').fill_null(False) | pl.col('beneficiary_in_wikiran').fill_null(False)).any().alias('has_wikiran'),
            (pl.col('sender_bank_is_bank_sensi').fill_null(False) | pl.col('receiver_bank_is_bank_sensi').fill_null(False)).any().alias('has_bank_sensi'),
            (pl.col('sender_in_crypto').is_in(['HIGH', 'MEDIUM', 'LOW']).fill_null(False) | pl.col('receiver_in_crypto').is_in(['HIGH', 'MEDIUM', 'LOW']).fill_null(False)).any().alias('has_crypto'),
        ])
        .sort('transaction_count', descending=True)
        .collect()
    )

    if result.is_empty():
        raise ValueError(f"No community found for BIC: {bic}")

    return [
        {
            'BIC': bic_upper,
            'Community ID': row['community_index'],
            'Transaction Count': row['transaction_count'],
            'risk_types': _risk_types_from_row(row),
        }
        for row in result.to_dicts()
    ]



# ── Specific Function for large communities returning only BIC-linked accounts and their neighbors ────────────────────────────────────────────────────────────────
def load_bic_neighborhood(path_data, bic, community_component):
    """
    For large communities, load only BIC-linked accounts and their
    direct neighbors (1-hop) instead of the full community.

    Args:
        path_data: path to the data directory
        bic: BIC code to center the graph on
        community_component: community index to filter within

    Returns:
        pd.DataFrame with transactions involving BIC accounts and their neighbors
    """
    if not bic:
        raise ValueError("BIC must be provided")

    filename = config["FILE_NAME"]
    filepath = os.path.join(path_data, filename)
    bic_upper = bic.strip().upper()
    community_component = int(float(community_component))

    logger.info(f"Loading BIC neighborhood for {bic_upper} in community {community_component}...")

    parquet_file = pq.ParquetFile(filepath)
    available_cols = [col for col in COLUMNS_TO_USE if col in parquet_file.schema.names]


    schema_names = set(parquet_file.schema.names)
    has_orig_bic = 'BIC_ORIGINATOR' in schema_names
    has_bene_bic = 'BIC_BENEFICIARY' in schema_names

    # Single scan — collect both sides at once
    bic_filter = None
    if has_orig_bic and has_bene_bic:
        bic_filter = (
            (pl.col('BIC_ORIGINATOR').str.to_uppercase() == bic_upper) |
            (pl.col('BIC_BENEFICIARY').str.to_uppercase() == bic_upper)
        )
    elif has_orig_bic:
        bic_filter = pl.col('BIC_ORIGINATOR').str.to_uppercase() == bic_upper
    elif has_bene_bic:
        bic_filter = pl.col('BIC_BENEFICIARY').str.to_uppercase() == bic_upper

    bic_accounts = set()
    if bic_filter is not None:
        select_cols = [c for c in ['ORIGINATOR_NEW_ACC_NUMBER', 'BENEFICIARY_NEW_ACC_NUMBER'] if c in schema_names]
        bic_rows = (
            pl.scan_parquet(filepath)
            .filter(
                (pl.col('community_index') == community_component) & bic_filter
            )
            .select(select_cols)
            .collect()
        )
        if 'ORIGINATOR_NEW_ACC_NUMBER' in select_cols:
            bic_accounts.update(bic_rows['ORIGINATOR_NEW_ACC_NUMBER'].drop_nulls().to_list())
        if 'BENEFICIARY_NEW_ACC_NUMBER' in select_cols:
            bic_accounts.update(bic_rows['BENEFICIARY_NEW_ACC_NUMBER'].drop_nulls().to_list())

    if not bic_accounts:
        logger.info(f"No accounts found for BIC {bic_upper} in community {community_component}")
        return pd.DataFrame()

    logger.info(f"Found {len(bic_accounts)} accounts directly owning BIC {bic_upper}")

    # Step 2: load all transactions where at least one side is a BIC-owning account
    # This gives BIC accounts + their direct neighbors — true 1 hop
    neighborhood = (
        pl.scan_parquet(filepath)
        .filter(
            (pl.col('community_index') == community_component) &
            (
                pl.col('ORIGINATOR_NEW_ACC_NUMBER').is_in(list(bic_accounts)) |
                pl.col('BENEFICIARY_NEW_ACC_NUMBER').is_in(list(bic_accounts))
            )
        )
        .select(available_cols)
        .collect()
    )

    if neighborhood.is_empty():
        logger.info("No neighborhood transactions found")
        return pd.DataFrame()

    df = neighborhood.to_pandas()
    del neighborhood
    gc.collect()

    df = optimize_dataframe_memory(df)

    logger.info(f"BIC neighborhood loaded: {len(df):,} transactions")
    logger.info(f"Memory usage: {df.memory_usage(deep=True).sum() / 1024**2:.2f} MB")

    return df
