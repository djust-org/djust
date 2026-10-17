- **The filter-parity differential's leak totals count live markup only.**
  `--compare` already split newly flagged cells into live markup and a
  payload fragment inside fully escaped text. The BEFORE and AFTER totals
  now use the same split: the headline counts cells with an unescaped tag
  opener, and the escaped-text cells are reported on their own line.
