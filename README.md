# Forward Branch Merge Check

Central reusable GitHub Actions workflows and scripts for checking forward-merge
health across an ordered branch chain.

The detailed documentation lives in:

```text
.github/forward-merge-check/README.md
```

Target repositories should normally copy only the small workflow stubs from:

```text
examples/target-workflows/
```

and call the reusable workflows from this repository.
