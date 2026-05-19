import polars as pl
from pathlib import Path

FILE_2025 = Path("")

FILES_2026 = [
    Path(""),
    Path(""),
    Path(""),
]

COLUMNS_TO_KEEP = [
    "",
    "",
    "",
]

# Columns to search for keywords (must be a subset of COLUMNS_TO_KEEP)
COLUMNS_TO_SCREEN = [
    "",
    "",
]

# Exact-match keywords (case-insensitive)
KEYWORDS = [
    "",
    "",
]

OUTPUT_FILE = Path("")

CSV_OPTIONS = dict(
    separator=",",
    infer_schema_length=10_000,
    try_parse_dates=True,
    truncate_ragged_lines=True,
)


def keyword_filter(df: pl.DataFrame, columns: list[str], keywords: list[str]) -> pl.DataFrame:
    """Keep rows where any of the given columns exactly matches any keyword."""
    kw_set = [kw.lower() for kw in keywords]
    mask = pl.lit(False)
    for col in columns:
        mask = mask | pl.col(col).cast(pl.Utf8).str.to_lowercase().is_in(kw_set)
    return df.filter(mask)


def main() -> None:
    all_files = [FILE_2025] + FILES_2026

    frames = [
        pl.scan_csv(f, **CSV_OPTIONS).select(COLUMNS_TO_KEEP)
        for f in all_files
    ]

    df = pl.concat(frames, how="diagonal_relaxed").collect(streaming=True)
    print(f"Total rows before filter : {df.height:,}")

    df = keyword_filter(df, COLUMNS_TO_SCREEN, KEYWORDS)
    print(f"Total rows after filter  : {df.height:,}")
    print(df.head(5))

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUTPUT_FILE, compression="zstd")
    print(f"Saved → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
