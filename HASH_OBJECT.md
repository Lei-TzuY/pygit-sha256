# Advanced `hash-object` plumbing

Phase 58 expands `pygit hash-object` from a single-file blob helper into a script-useful object-construction primitive for pygit's SHA-256 database.

## Files and multiple inputs

```console
pygit hash-object README.md
pygit hash-object README.md pyproject.toml
pygit hash-object -w README.md
```

Each input produces one 64-character SHA-256 object ID, in argument order. `-w` writes the object into the current repository; hash-only mode does not require a repository.

## Standard input

Hash one raw payload directly from stdin:

```console
printf 'hello\n' | pygit hash-object --stdin
```

The bytes are consumed exactly; no newline is added or removed by `hash-object`.

For batch file hashing, `--stdin-paths` reads one newline-delimited filesystem path per line:

```console
printf 'README.md\npyproject.toml\n' | pygit hash-object --stdin-paths
```

Blank lines are ignored. File names containing spaces are preserved.

## Native object types

The default type is `blob`. The four native pygit object types are accepted explicitly:

```console
pygit hash-object -t blob file.bin
pygit hash-object -t tree -w tree.raw
pygit hash-object -t commit -w commit.raw
pygit hash-object -t tag -w tag.raw
```

The object ID is computed from the exact envelope:

```text
<type> <payload-size>\0<payload>
```

using SHA-256.

Structured `tree`, `commit`, and `tag` payloads are parsed and sanity-checked before hashing or writing. This deliberately rejects obviously malformed native objects instead of populating the store with data that pygit cannot read back. Arbitrary unknown object types and Git's `--literally` escape hatch are outside this phase.

## Python API

```python
from pygit import hash_object_data, object_envelope, write_object_data

oid = hash_object_data(b"hello\n")
stored = write_object_data(repo, b"hello\n", "blob")
assert oid == stored
assert object_envelope(b"hello\n") == b"blob 6\0hello\n"
```

`hash_path()` is also available for direct filesystem inputs. Writes are content-addressed and idempotent.

## Scope boundary

Phase 58 intentionally does not implement clean/smudge filters, `--path`, replacement objects, or arbitrary malformed object types. Those features depend on configuration/filter machinery rather than the core object hashing contract targeted here.
