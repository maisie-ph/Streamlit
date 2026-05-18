"""
Load only the transactions within N hops of a given account,
using N lazy Polars passes on the parquet file (no full community load).

Args:
    path_data: path to the data directory
    account_id: the starting node entity ID (string)
    n_hops: number of hops to expand (1 to 5)

Returns:
    pd.DataFrame with the same schema as load_community_data()
"""

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


# ── Columns to use ────────────────────────────────────────────────────────────────
COLUMNS_TO_USE = [
    'ORIGINATOR_ENTITY_ID',
    'ORIGINATOR_NAME',
    'ORIGINATOR_ACC_NUMBER',
    'ORIGINATOR_NEW_ACC_NUMBER',
    'BENEFICIARY_ENTITY_ID',
    'BENEFICIARY_NAME',
    'BENEFICIARY_ACC_NUMBER',
    'BENEFICIARY_NEW_ACC_NUMBER',
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
    'community_index',
    'BIC_ORIGINATOR',
    'Orig_in_Sayari',
    'Bene_in_Sayari',
    'BIC_BENEFICIARY'

]

def load_subgraph_data(path_data, account_id, n_hops=2, community_index=None):

    filepath = os.path.join(path_data, config["FILE_NAME"])
    account_id = str(account_id)

    logger.info(f"Loading subgraph for account {account_id} with {n_hops} hop(s)...")
    parquet_file=pq.ParquetFile(filepath)
    available_cols = [col for col in COLUMNS_TO_USE if col in parquet_file.schema.names]


    known_nodes = {account_id}
    frontier={account_id}
    all_transactions=[]

    # N lazy passes — each hop expands the set of known nodes
    for hop in range(n_hops):
        logger.info(f"Hop {hop + 1}/{n_hops} — known nodes: {len(known_nodes)}")

        if not frontier:
            logger.info("Empty frontier, stopping early")
            break

        # query only transactions touching the current frontier (not all known nodes)
        scan = pl.scan_parquet(filepath)
        if community_index is not None:
            scan = scan.filter(pl.col('community_index') == community_index)
        result = (
            scan
            .filter(
                pl.col('ORIGINATOR_NEW_ACC_NUMBER').is_in(list(frontier)) |
                pl.col('BENEFICIARY_NEW_ACC_NUMBER').is_in(list(frontier))
            )
            .select(available_cols)
            .collect()
        )

        if result.is_empty():
            logger.info(f"No new nodes found at hop {hop + 1}, stopping early")
            break

        all_transactions.append(result)

        # Expand known nodes with newly discovered neighbors
        new_origs = set(result['ORIGINATOR_NEW_ACC_NUMBER'].drop_nulls().to_list())
        new_benes = set(result['BENEFICIARY_NEW_ACC_NUMBER'].drop_nulls().to_list())
        new_nodes = (new_origs | new_benes) - known_nodes

        known_nodes= known_nodes | new_nodes
        frontier= new_nodes

        del result
        gc.collect()

    logger.info(f"Subgraph covers {len(known_nodes)} distinct nodes after {n_hops} hop(s)")

    if not all_transactions:
        logger.info("No transactions found for the subgraph")
        return pd.DataFrame()
    

    # Concat
    combined= pl.concat(all_transactions).unique()
    subgraph_df= combined.to_pandas()


    del combined, all_transactions
    gc.collect()

    if subgraph_df.empty:
        logger.info("no transactions found for the subgraph")
        return pd.DataFrame()

    subgraph_df = optimize_dataframe_memory(subgraph_df)
  
    logger.info(f"Subgraph loaded: {len(subgraph_df):,} transactions")
    logger.info(f"Memory usage: {subgraph_df.memory_usage(deep=True).sum() / 1024**2:.2f} MB")

    return subgraph_df