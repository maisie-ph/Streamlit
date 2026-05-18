"""
Batch node-color computation — one vectorised pass over the whole dataframe
instead of filtering the df once per account.
Returns a dict {account_id: hex_color} ready for graph_generator to consume.
"""

import pandas as pd

# Priority order: Sayari > FSI > PEP > WIKI > SSD/SSZ Bank > Crypto > default
_PRIORITY = [
    ('is_sayari',     '#0018F9'),
    ('is_scrutiny',   '#FF0000'),
    ('is_pep',        '#086776'),
    ('is_wikiran',    '#FF5F15'),
    ('is_bank_sensi', '#3c1414'),
    ('is_crypto',     '#00FF00'),
]
_DEFAULT_COLOR = '#DBDBDB'


def get_node_colors_batch(df: pd.DataFrame) -> dict:
    """
    Return {account_id: color} for every account that appears in df.
    Single vectorised pass — O(rows) instead of O(nodes × rows).
    """
    orig = df[['ORIGINATOR_NEW_ACC_NUMBER',
               'Orig_in_Sayari', 'sender_under_scrutiny', 'sender_is_pep',
               'originator_in_wikiran', 'sender_bank_is_bank_sensi',
               'sender_in_crypto']].copy()
    orig.columns = ['account', 'is_sayari', 'is_scrutiny', 'is_pep',
                    'is_wikiran', 'is_bank_sensi', 'crypto_raw']

    bene = df[['BENEFICIARY_NEW_ACC_NUMBER',
               'Bene_in_Sayari', 'receiver_under_scrutiny', 'receiver_is_pep',
               'beneficiary_in_wikiran', 'receiver_bank_is_bank_sensi',
               'receiver_in_crypto']].copy()
    bene.columns = ['account', 'is_sayari', 'is_scrutiny', 'is_pep',
                    'is_wikiran', 'is_bank_sensi', 'crypto_raw']

    combined = pd.concat([orig, bene], ignore_index=True).dropna(subset=['account'])
    combined['is_crypto'] = combined['crypto_raw'].isin(['HIGH', 'MEDIUM', 'LOW'])

    flag_cols = ['is_sayari', 'is_scrutiny', 'is_pep', 'is_wikiran', 'is_bank_sensi', 'is_crypto']
    for col in flag_cols:
        combined[col] = combined[col].fillna(False).astype(bool)

    agg = combined.groupby('account')[flag_cols].any()

    # Apply priority: first True flag wins
    colors = {}
    for account, row in agg.iterrows():
        for flag, color in _PRIORITY:
            if row[flag]:
                colors[str(account).strip()] = color
                break
        else:
            colors[str(account).strip()] = _DEFAULT_COLOR

    return colors
