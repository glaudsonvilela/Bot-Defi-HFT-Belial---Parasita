# Bot Belial — Estado atual

## Continuidade

Continuação direta do projeto **Belial 0.0.5**.

Raiz operacional local:

```text
/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter
```

## Segurança operacional

- PAPER / SHADOW somente.
- Nenhuma ordem financeira real nesta fase.
- Contratos/testes congelados não são relaxados para fazer instalação passar.
- Backups, hashes, instaladores e evidências locais são preservados.

## A4 FIX4

O A4 permanece com **24 ativos**.

O FIX4 fecha especificamente a regressão observada no FIX3:

```text
esperado: ['src/main.rs']
FIX3:     ['src/main.rs', 'src/raydium_protocols.rs']
FIX4:     ['src/main.rs']
```

`engine_a4/src/raydium_protocols.rs` é restaurado byte-a-byte de
`engine_a2/src/raydium_protocols.rs`.

Gates locais do pacote FIX4:

- suítes offline: PASS;
- grupo A4: PASS;
- Rust native test/build: PASS;
- release imutável: `belial-a4-90m-v1-fix4`;
- ordens: false.

A captura de mercado de 90 minutos não é declarada por este checkpoint.

## Próxima linha

A próxima linha é **BELIAL_A5_16Q**, isolada do A4.

Universo:

```text
FARTCOIN BOME WIF POPCAT
RAY JUP BONK PENGU
PUMP USELESS GRASS PYTH
RENDER KMNO MEW MPLX
```

Direção:

- reduzir fan-out para 16 pools;
- melhorar freshness e estabilidade;
- impedir desaparecimento visual de preço sem inventar cotação;
- terminal Bloomberg F1/F2/F3/F4 com caminho visual em Rust;
- N2 fast path;
- sensores externos somente SHADOW inicialmente.
