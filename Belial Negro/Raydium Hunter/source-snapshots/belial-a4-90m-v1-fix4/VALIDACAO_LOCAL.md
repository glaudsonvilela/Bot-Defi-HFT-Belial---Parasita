# Validação local — Belial A4 / recovery por evento e estado dos livros

## Escopo

A4 corrige o bloqueio excessivamente amplo de ator observado na A3 e a semântica visual que confundia `BOOK_STALE` com queda do transporte. A estratégia, sizing, stops/trailing e 32 cabeças N2 permanecem congelados. O teste é PAPER/shadow por 90 minutos.

## Testes efetivamente executados neste ambiente

**939 testes Python aprovados, sem skips.** Foram reexecutadas as suítes herdadas e a nova suíte A4.

| Suíte | Testes |
|---|---:|
| mp12 | 49 |
| n2_mp12 | 22 |
| d2 | 4 |
| runner | 287 |
| perp | 50 |
| a1 | 88 |
| a2 | 97 |
| n2_a2 | 22 |
| labels_a2 | 21 |
| a3 | 140 |
| a4 | 159 |
| **Total** | **939** |

Logs estão em `validation/*_tests.log`; resumo em `TEST_RESULTS_A4.json`.

## Recovery por evento

A suíte A4 verifica que:

- falha isolada de RPC do ator incrementa o contador local ao evento e não impõe o warm-up da fonte inteira;
- evento de protocolo/decode colocado em quarentena permanece inelegível, sem virar sinal;
- overflow da fila de ator continua sendo falha de fonte e exige reset/recuperação;
- desconexão real de WebSocket continua sendo falha de fonte e exige reset/recuperação;
- boundary nativo `a2_actor_not_before_ms` continua sendo respeitado quando existe uma falha verdadeira de continuidade;
- o writer nativo registra separadamente `a4_event_local_fault_ms` e `a4_event_local_faults`;
- analyzer não converte o contador local de falhas em sinal ou entrada.

O trade-off deve permanecer explícito: um evento que não pôde ser resolvido é desconhecido, não “bom”. A4 descarta esse evento e preserva os demais contextos já resolvidos. Isso reduz falso bloqueio de pool, mas não transforma o evento perdido em evidência causal. Por esse motivo a alteração permanece em PAPER para avaliação OOS.

## Livros e transporte

A4 testa a classificação de apresentação:

- `FRESH`
- `STALE`
- `RECONNECTING`
- `DISCONNECTED`
- `INVALID`

`STALE` nunca relaxa a idade máxima de execução. O limite de 2.000 ms continua no modelo de PAPER. A UI não usa mais o estado da conexão SSE do navegador como substituto do transporte de Hyperliquid/Lighter. O estado da venue e o estado do instrumento são campos separados.

Os testes validam que um instrumento pode ficar `STALE` enquanto a venue permanece `CONNECTED`, situação observada na A3 com WIF/Lighter. Não se introduziu um freshness adaptativo nesta release; isso exige distribuição real de inter-update por mercado antes de alterar o gate.

## Preservação

`docs/PRESERVATION_A4.json` contém hashes dos arquivos herdados preservados. `engine_a4` é uma cópia isolada do engine A2, com alteração intencional apenas em `src/main.rs` para separar falhas locais de evento das falhas de continuidade da fonte. `engine_a2` e a árvore A3 original permanecem preservados.

Não se afirma byte-identidade do novo engine A4 com A2. Os parâmetros econômicos/thresholds continuam herdados e os testes A4 verificam que a mudança não introduz novos thresholds de entrada/saída.

## O que NÃO foi executado aqui

Este ambiente não possui `cargo/rustc`, a GPU NVIDIA do usuário ou conectividade aos feeds públicos utilizados pelo Belial. Portanto não foram executados aqui:

- compilação/testes Rust do novo `engine_a4/src/main.rs`;
- kernels OpenCL na GTX 1060 do usuário;
- handshake público Solana/Hyperliquid/Lighter;
- resistência real de 90 minutos;
- ordens reais ou acesso a carteira.

O executor roda os testes offline antes da instalação e o build worker executa os gates nativos no computador do usuário. Falha de Cargo/build deixa o componente indisponível; não é convertida em sucesso pelo total de testes Python.

## Critério para a próxima captura

A A4 deverá ser avaliada por evidência, não pela quantidade de trades desejada. No ZIP final, os pontos principais são:

1. redução de `actor_context_recoveries` e `rejected_unavailable_source` sem crescimento indevido de sinais inválidos;
2. contagem de `a4_event_local_faults` e quais símbolos/eventos foram descartados;
3. `wallet_exits -> ARM -> confirmations -> episodes` comparável com A2/A3;
4. livros por estado FRESH/STALE e transporte por venue;
5. N2 prospectiva ativa, labels por horizonte e censura causada por gaps reais;
6. replay PAPER e integridade da sessão.

Nenhuma dessas métricas, isoladamente, prova lucratividade ou prontidão para dinheiro real.
