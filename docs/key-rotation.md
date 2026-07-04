# Field-encryption key rotation

`EncryptedString` columns (today: `APIKey.secret`) are Fernet-encrypted at
rest by `resilience_kit.crypto.FernetCipher`, which is backed by
`cryptography.fernet.MultiFernet` as of resilience-kit 0.2.0. Key material
is an **ordered list**: the first key is the *primary* (it encrypts new
writes); every key in the list is tried on decrypt. That is what keeps the
*read/write path* available throughout rotation — ciphertext written under a
retired key still decrypts while new writes move onto the new key. (The
bulk re-encryption in step 2 is batched rather than one long transaction —
see that step — so it does not lock a large table end-to-end either.)

Configure the list via:

```bash
RESILIENCE_CRYPTO__FIELD_ENCRYPTION_KEYS=["<primary>","<older>"]
```

The singular `RESILIENCE_CRYPTO__FIELD_ENCRYPTION_KEY` (and the legacy
`FIELD_ENCRYPTION_KEY` alias mapped by `legacy_env_alias()`) still work for
one minor cycle but emit a `DeprecationWarning`; migrate to the list form.

## Rotation runbook

### 1. Prepend the new key

Generate a real Fernet key and put it first, keeping the old key so
existing ciphertext still decrypts:

```bash
K_NEW=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
RESILIENCE_CRYPTO__FIELD_ENCRYPTION_KEYS=["$K_NEW","$K_OLD"]
```

Deploy. New writes now use `K_NEW`; reads still resolve `K_OLD` ciphertext.

### 2. Re-encrypt stored ciphertext

Run the rotation command. It walks every registered `EncryptedString`
column at the SQL layer (never materialising plaintext) and calls
`FernetCipher.rotate()` on each token:

```bash
python -m src.management.rotate_encryption --dry-run   # count affected rows
python -m src.management.rotate_encryption             # rotate + commit
```

The sweep is **batched**: each table is walked by keyset pagination on its
primary key (`_ROTATE_BATCH_SIZE` rows at a time) and each batch commits in
its own short transaction. Locks are released between batches, so a large
encrypted table rotates without a table-long write transaction blocking
concurrent writers on the auth hot path. `--dry-run` is a read-only pass —
it opens no write transaction at all.

Safe to re-run. If a token cannot be decrypted under the configured keys the
run aborts: the current batch rolls back, but batches already committed stay
rotated. Because rotation is idempotent (Fernet tokens carry a fresh
timestamp/IV each write), just fix the key list — the offending key is
missing — and re-run; the already-rotated rows re-encrypt harmlessly.

### 3. Drop the retired key

Once step 2 has verified-committed for every environment sharing the data:

```bash
RESILIENCE_CRYPTO__FIELD_ENCRYPTION_KEYS=["$K_NEW"]
```

Deploy. `K_OLD` is fully retired.

## Migrating off the deprecated singular / passphrase key

If you were on the singular `FIELD_ENCRYPTION_KEY` (or a passphrase, which
resilience-kit SHA-256-derives on the deprecated path):

1. Generate a real Fernet key `K_NEW`.
2. Set `FIELD_ENCRYPTION_KEYS=["<K_NEW>","<old-value>"]` — the old value is
   still resolved on decrypt, so existing data reads while new data uses
   `K_NEW`.
3. Run step 2 above.
4. Drop the old value: `FIELD_ENCRYPTION_KEYS=["<K_NEW>"]`.

## Gotchas

- **Never drop a key before re-encrypting.** A token under a dropped key
  raises `DecryptionError` on the next read.
- The cipher is process-cached (`functools.lru_cache`). Changing keys
  requires a redeploy / restart to take effect.
- Add any new `EncryptedString` column to the `ENCRYPTED_COLUMNS` registry
  in `src/management/rotate_encryption.py` so the sweep covers it.

See also: the upstream runbook in resilience-kit `docs/key-rotation.md`
and ADR-0014 (MultiFernet rotation).
