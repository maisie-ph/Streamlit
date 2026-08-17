import polars as pl
# (swap to pandas if you're not on Polars for this dataset — see note below)

def rename_to_schema(df: pl.DataFrame, column_mapping: dict[str, str], strict: bool = True) -> pl.DataFrame:
    """
    Rename columns from the new (production) dataset to match
    the column names used in the existing test-data codebase.

    Parameters
    ----------
    df : pl.DataFrame
        The new production dataset, with its own column names.
    column_mapping : dict
        Maps {production_col_name: test_col_name}.
        Only needs to include columns whose names actually differ.
    strict : bool
        If True, raises if a mapped source column is missing from df
        (catches typos/schema drift early). If False, just skips missing ones.

    Returns
    -------
    pl.DataFrame with columns renamed to match your existing schema.
    """
    existing = set(df.columns)
    applicable = {}
    missing = []

    for prod_col, test_col in column_mapping.items():
        if prod_col in existing:
            applicable[prod_col] = test_col
        else:
            missing.append(prod_col)

    if missing and strict:
        raise ValueError(f"Expected columns not found in production data: {missing}")
    elif missing:
        print(f"Warning: skipped missing columns: {missing}")

    return df.rename(applicable)


# --- usage ---
COLUMN_MAPPING = {
    # production_name : test_name (the one your code expects)
    "TRANSACTION_ID": "transaction_id",
    "CUST_NAME": "customer_name",
    "AMT": "amount",
    # ... fill in the rest
}

df_prod = pl.read_parquet("production_data.parquet")
df_prod_aligned = rename_to_schema(df_prod, COLUMN_MAPPING)
