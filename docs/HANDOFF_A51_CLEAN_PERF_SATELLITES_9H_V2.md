# Bot Belial — A5.1 Clean/Perf + Satélites + 9H

Base congelada: `eb1beadeedd0f81f8ab1b882ac86c5fc39332386`

## Preservado

- A5 16Q original byte-idêntico;
- thresholds, sizing e políticas de saída inalterados;
- PAPER / SHADOW;
- orders=false.

## Limpeza / performance

- cache JSON mtime/size em status hot-path;
- ANALYSIS_BLOCKED e aggregate/status lidos uma vez por ciclo de status;
- N2 heads estáticos lidos uma vez;
- books.json de display: 10 Hz; state/platform: 2 Hz;
- curvas do painel limitadas a 240 pontos; histórico fechado a 60 posições;
- SSE coalesce payload serializado idêntico;
- dashboard A5.1 servido de `a51/web/dashboard.html`;
- Rust formatado e validado com check/clippy/test/build.

## Satélites

- Binance USDⓈ-M Futures: SHADOW;
- Bybit linear: SHADOW;
- Kraken Futures: SHADOW;
- somente endpoints públicos GET;
- sem API keys;
- sem ordens;
- não criam, bloqueiam, dimensionam ou encerram trades;
- last-good pode ser exibido como STALE; ausência de dado nunca vira zero;
- funding/OI brutos não são agregados entre venues com unidades/intervalos diferentes.

## Sessão

Após o checkpoint Git, o instalador inicia 540 minutos / 9 horas. Antes de
consumir as 9h, exige que o caminho principal tenha observado pelo menos um
sensor vivo, um livro utilizável e um conjunto de pré-requisitos PAPER.
