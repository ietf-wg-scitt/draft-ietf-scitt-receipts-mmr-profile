# Checking the python blocks

`check_python_blocks.py` extracts every python block from the draft, runs it,
and checks the results against an MMR built independently (standard library
only):

- `add_leaf_hash` builds the same node values as the reference;
- `index_height` matches the structural height, and `index_height(n) == 0`
  exactly when MMR(n) is complete;
- `peaks`, `leaf_count` and `mmr_size_for_leaf_count` match the reference;
- `inclusion_proof_path` with `included_root` proves every node to its peak at
  every complete size;
- `consistency_proof_paths` with `consistent_roots` completes the target
  accumulator for every pair of complete sizes, including from 0;
- each `# if ... -> ERROR` rejection in `consistent_roots` fires on material
  made wrong in exactly that way (the comments are run as checks).

`known-answers.json` holds values computed by the blocks: the node values of
MMR(39), inclusion proofs (including empty paths), consistency proofs
(including from 0 and with several origin peaks), and the confusable target
sizes where the fold accepts the same material (binding the size is the signed
tree-size-2's job). Leaf n is SHA-256 of n as an 8-byte big-endian integer.

    python3 tools/check_python_blocks.py --check-vectors tools/known-answers.json
    python3 tools/check_python_blocks.py --write-vectors tools/known-answers.json

The script reports what it cannot run as written: `ones_count` is described
in prose only, so the script supplies it, and the `trailing_zeros` block is an
expression rather than a function.
