# Reader fixtures

- `lehamim/tinyfish_fetch.json`: valid JSON reduction of `fixtures/golden/lehamim/tinyfish_fetch2.json`.
  The golden capture is truncated mid-document by a tool "lines omitted" marker, so it cannot be parsed;
  this copy keeps its first 34 lines (home page result, links cut short) and its `llms.txt` 404 error.
- `lehamim/jsonld.json`: expected `python -m arf.readers replay fixtures/golden/lehamim` output.

All files are recorded data; tests make no live calls.
