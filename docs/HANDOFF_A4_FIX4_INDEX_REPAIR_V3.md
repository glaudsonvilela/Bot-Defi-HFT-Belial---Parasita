# Bot Belial - A4 FIX4 Index/Git Repair V3

UTC: 20260926T012948Z

## Causa confirmada

O snapshot físico A4 FIX4 já estava completo e válido:

```text
expected_members=318
physical_members=318
physical_missing=0
physical_extra=0
physical_bad_hash=0
A4_PACKAGE_HASHES=PASS
```

O defeito estava somente na árvore rastreada pelo commit **69fe351**.

Os 26 logs de validação são membros legítimos do
`MANIFEST_SHA256.json`, mas o repositório possui `*.log` no
`.gitignore`. Como o snapshot FIX4 era um caminho novo, o fechamento usou
`git add` normal e esses 26 arquivos ficaram fora do commit.

## Prova do HEAD

```text
HEAD=69fe351269639abb6a770a38bfe66c53a21f0eb3
HEAD_MISSING_VALIDATION_LOGS=26
ROOT_CAUSE=IGNORED_CANONICAL_LOGS_NOT_STAGED_IN_NEW_FIX4_SNAPSHOT
```

Cada um dos 26 arquivos:

1. já existia fisicamente no snapshot FIX4;
2. batia com o SHA256 do manifesto;
3. batia byte-a-byte com a release imutável do SSD;
4. estava ignorado pelo Git.

## Reparo

Foi usado `git add -f` somente nesses 26 membros canônicos.

Antes de qualquer commit, o INDEX staged foi convertido em uma árvore temporária
com `git write-tree`, exportado com `git archive` e validado pelo verifier
oficial A4.

```text
STAGED_INDEX_A4_CANONICAL=true
A4_PACKAGE_HASHES=PASS
```

## Nao alterado

- MANIFEST_SHA256.json;
- engine_a4;
- pools/universo;
- estratégia;
- testes;
- thresholds;
- sizing;
- políticas de saída;
- PAPER / SHADOW;
- orders=false.

## A5

O A5 bootstrap recusou corretamente o commit incompleto.

Depois do commit/push deste reparo, o bootstrap A5 deve ser reancorado no novo
HEAD. Não reutilizar o EXPECTED_HEAD 69fe351.
