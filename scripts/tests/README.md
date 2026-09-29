# `scripts/tests/`

| File | What it checks | Needs |
|------|----------------|-------|
| `test_inject.sh` | `../inject.sh`: encoder, opcode collisions, every backend, tree edits | bash only |
| `test_attn_contract.py` | the shipped `attn` instruction against the source trees | pytest |
| `attn.c` | input for the built compiler | built toolchain |

```bash
bash scripts/tests/test_inject.sh
python -m pytest scripts/tests/test_attn_contract.py -q
```
