"""
Helper function to format transaction amounts.
"""


def format_amount(amount):
    """
    Formatting the transaction amount in a readable format
    """
    if amount >= 1_000_000:
        return f"{amount/1_000_000:.2f} M €"
    elif amount >= 1_000:
        return f"{amount/1_000:.2f} K €"
    else:
        return f"{amount:.2f} €"