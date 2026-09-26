# Bot Belial - Handoff A5 16Q Bootstrap

Gerado em **20260926T021135Z** por `BELIAL_A5_16Q_BOOTSTRAP_V9_COMPLETO.sh`.

## Base congelada

```text
A4 FIX4 commit: bd554ed580062bcfcd12bb0751ff516e3b8b0faa
A4 release: belial-a4-90m-v1-fix4
```

O A4 nao foi modificado.

## Universo A5 16Q

```text
FARTCOIN BOME WIF POPCAT
RAY JUP BONK PENGU
PUMP USELESS GRASS PYTH
RENDER KMNO MEW MPLX
```

## Mudancas intencionais

- namespace Python novo: `a5/`;
- engine novo: `engine_a5/`;
- testes novos: `tests_a5/`;
- universo canonico 24 -> 16 no A5;
- FOCUS4 = RENDER, KMNO, MEW, MPLX;
- limite RAW nativo A5: 16 (A4 FIX4: 24);
- limite WS relay A5: 16;
- dashboard A5 mostra 16 candidatos e grupo FOCUS4.

## Nao alterado

- thresholds de estrategia;
- sizing;
- politicas de saida;
- duracao de 90 minutos;
- PAPER / SHADOW;
- ordens reais continuam desabilitadas.

## Gates

- A4 source hash gate: PASS;
- A4 critical preservation: PASS;
- A5 16Q structural gate: PASS;
- A4 regression tests: PASS;
- A5 16Q targeted smoke: PASS;
- A5 tests: PASS;
- Rust engine_a5 test/build: PASS;
- captura de mercado: NAO INICIADA.

## Hashes

```text
A5 MANIFEST  bee1cbd2e57bc159a372a3fdad8dd317cff717589818bda1006948f56473a2ae
A5 main.rs   c3756da69e8f751a53bd9f9cddc1f792f429d05bf3fce393bf327e96d46ae02b
A5 raw.rs    76e5125920cd376247946e4f5c5775769dd1a0d870d806a452ba6e9bb36c948e
installer    6d160efa9420f9ac527158cf6b7ec8ce80f6f058b10d66b5018b991148834c0f
```

## Proximo passo

Depois do checkpoint Git deste bootstrap, criar/rodar o gate de sessao
`BELIAL_A5_16Q_90M`.
