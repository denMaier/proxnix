# proxnix git hooks

Install the repo-managed hooks with:

```bash
./ci/install-git-hooks.sh
```

Current hooks:

- `pre-push`: validates release tags matching `v*` before they are pushed
