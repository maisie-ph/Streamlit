"""
Helper function to reduce dataframe memory usage 
"""
import pandas as pd

def optimize_dataframe_memory(df):
    for col in ['ORIGINATOR_NEW_ACC_NUMBER', 'BENEFICIARY_NEW_ACC_NUMBER', 'community_index']:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
            if df[col].nunique() / len(df) < 0.5:
                df[col] = df[col].astype('category')

    if 'EXECUTION_GLOBAL_DATE_TIME' in df.columns:
        if df['EXECUTION_GLOBAL_DATE_TIME'].dtype == 'object':
            df['EXECUTION_GLOBAL_DATE_TIME'] = pd.to_datetime(
                df['EXECUTION_GLOBAL_DATE_TIME'],
                errors='coerce'
            )

    return df
