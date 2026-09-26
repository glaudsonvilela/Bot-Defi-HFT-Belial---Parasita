# Belial A4 — recuperação por evento + estados reais de mercado

A4 é uma release **PAPER/shadow** de 90 minutos para corrigir dois gargalos observados ao vivo na A3 sem alterar os thresholds da estratégia: (1) uma falha isolada de resolução de ator não pode congelar a fonte inteira por 60 s; (2) um livro temporariamente velho não pode ser mostrado como se a conexão da venue tivesse caído.

## O que permanece congelado

- Universo configurado: CORE12 + EXPANSION12 (24 candidatos; disponibilidade real continua sendo pré-validada).
- Detecção/ARM/confirmação, deduplicação, Economic Gate e sizing herdados.
- Três políticas de saída: STOP50/TRAIL35, STOP75/TRAIL50 e STOP100/TRAIL75.
- Capital e notional de pesquisa herdados da A2/A3; esta release **não** transforma automaticamente o teste em capital de US$ 50.
- 32 cabeças N2 e seus horizontes/thresholds.
- N2 continua consultiva e não aprova/rejeita ordens.
- PAPER only: nenhuma carteira real, nenhuma ordem real, nenhuma transação on-chain enviada.
- `book_max_age_ms=2000` continua sendo o limite de segurança para aceitar livro em fills PAPER. A4 não torna livro stale elegível.

## Correção 1 — falha de ator é local ao evento quando a continuidade da fonte não foi perdida

Na A3, mudanças em contadores como `rpc_errors` e certos `decode_errors` eram interpretadas como perda de integridade da fonte. O motor apagava o histórico de ator já resolvido daquele símbolo e impunha um `ACTOR_WARMUP_AFTER_EVENT_GAP` de 60 s. Em uma fonte com falhas esparsas, isso podia manter a pool quase continuamente inelegível, mesmo quando o WebSocket continuava recebendo eventos.

A4 separa duas classes:

**Evento local** — a transação/evento que não pôde ser resolvido ou validado é descartado e contado; histórico de outros eventos resolvidos do símbolo não é apagado e a pool inteira não recebe warm-up de 60 s.

**Falha de continuidade da fonte** — desconexão real do WebSocket, overflow da fila de atores, quebra de continuidade/epoch ou outro defeito que impeça afirmar que a sequência recebida está íntegra continua fazendo reset da fonte e warm-up antes de permitir novos sinais.

Isso é uma mudança na **semântica de disponibilidade**, não nos thresholds do detector. O evento desconhecido não vira swap, wallet exit, ARM ou confirmação. Também não é uma alegação de causalidade perfeita: um evento irresolvido pode conter informação que não conhecemos. Por isso a A4 continua PAPER e expõe `a4_event_local_faults` para medir essa incerteza em vez de escondê-la.

## Correção 2 — FRESH, STALE, RECONNECTING e DISCONNECTED

A interface separa o transporte da venue do frescor de cada instrumento:

- `FRESH`: transporte conectado e livro dentro do limite de idade; pode ser elegível para fill se os demais gates passarem.
- `STALE`: transporte conectado, mas aquele instrumento não atualizou dentro do limite; **não é elegível para fill**.
- `RECONNECTING`: worker/transporte da venue está tentando restabelecer a conexão.
- `DISCONNECTED`: transporte indisponível.
- `INVALID`: livro estruturalmente inválido/incompleto.

A conexão SSE do dashboard é mostrada separadamente e nunca é usada como prova de conexão do mercado.

O diagnóstico ao vivo que motivou a A4 mostrou Hyperliquid e Lighter conectados enquanto um único livro WIF/Lighter ficou temporariamente `BOOK_STALE`; depois voltou a atualizar com `gaps=0`. A4 conserva essa distinção em vez de pintar o evento como queda da venue.

## Agente N2

A correção de ciclo de vida da A3 é mantida: canais podem nascer depois do processo, previsões são persistidas antes de avaliação, labels/updates/heartbeat são expostos e checkpoints só reutilizam gerações **A4** completas e compatíveis. A4 não importa automaticamente pesos A1/A2/A3 porque o contrato da release inclui o código A4.

Atividade do pipeline não prova vantagem econômica. `predictions > 0`, `valid_labels > 0` e `training_updates > 0` significam que o agente está funcionando, não que melhora a estratégia.

## Instalação e execução

O instalador é isolado em:

`/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter/releases/belial-a4-90m-v1`

Ele recusa iniciar uma A4 se encontrar outro coletor Belial ativo. Para substituir uma A3 em andamento, encerre a A3 pelo terminal original com `Ctrl+C`, deixe-a finalizar o que conseguir e aguarde o prompt voltar; depois execute a A4.

```bash
bash "$HOME/Área de trabalho/Altside/BELIAL_A4_90M_EXECUTAR.sh"
```

A preparação roda testes e builds nativos no computador do usuário antes da captura. A janela de 90 minutos começa apenas depois da preparação. Finalização/replay/compactação podem acrescentar tempo ao cronômetro.

Saída esperada:

`$HOME/Área de trabalho/Altside/BELIAL_A4_90M_RESULTADO_*.zip`

## Validação e limites

Neste ambiente foram executados 939 testes Python sem skips, incluindo as 780 verificações herdadas da A3 e 159 testes A4. Não há `cargo/rustc`, GPU NVIDIA nem acesso ao mercado público aqui; portanto **o novo `engine_a4/src/main.rs` ainda não foi compilado neste ambiente**. O executor mantém o gate de Cargo no computador do usuário. Veja `VALIDACAO_LOCAL.md` e `TEST_RESULTS_A4.json`.
