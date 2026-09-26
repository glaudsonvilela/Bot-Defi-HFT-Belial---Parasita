# Bot Belial — Handoff A4 FIX4

Gerado por `BELIAL_A4_90M_FIX4_ATUALIZAR.sh` em **20260926T010439Z**.

## Gate encerrado

- PAPER / SHADOW: **preservado**
- ordens reais: **desabilitadas**
- universo A4 congelado: **24 ativos**
- delta nativo permitido: **somente `engine_a4/src/main.rs`**
- `engine_a4/src/raydium_protocols.rs`: **byte-idêntico ao A2**
- suítes offline: **PASS**
- Rust native test/build: **PASS**
- release imutável: `/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter/releases/belial-a4-90m-v1-fix4`

## Hashes críticos

```text
MANIFEST_SHA256.json  17180c539e8c8b3699065b1c1adc614353946b1577cd36e14172afb019dba34d
engine_a4/main.rs     823651bfbcd33add2429cfe6952f5b67cd0137fa6dd20536f42683c649bc6625
raydium_protocols.rs  6e1521b747d4066e41eff0cecf82547f9acf9ebd4ba35fc53d58ae766e8774e0
installer             61cdc09c626d60a9ebc8229eb38c78d0cf11065901552d9d61b442d389647101
```

## Próximo estágio

Não modificar o A4 para reduzir pools.

A redução nasce em linha isolada **BELIAL_A5_16Q**:

```text
FARTCOIN
BOME
WIF
POPCAT
RAY
JUP
BONK
PENGU
PUMP
USELESS
GRASS
PYTH
RENDER
KMNO
MEW
MPLX
```

Direção já aprovada para A5:

1. 16 pools, qualidade acima de quantidade;
2. terminal Bloomberg preservando F1 Mercado / F2 Posições / F3 Agente / F4 Fontes;
3. caminho visual em Rust;
4. last-good-price + freshness explícita, sem inventar preço;
5. N2 fast path sem JSON/filesystem no caminho quente;
6. sensores externos primeiro em SHADOW;
7. nenhuma promoção para execução real sem novo gate explícito.

## Runtime

Este checkpoint comprova fechamento de pacote/test/build local.
Ele **não** afirma que uma nova sessão de mercado de 90 minutos foi executada.
