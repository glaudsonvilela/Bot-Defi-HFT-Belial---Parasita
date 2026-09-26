# Validação local — Belial A3 / agente e terminal

## Entregue
Ciclo de vida N2 corrigido, previsão causal persistida, heartbeat/contadores/placar por horizonte, checkpoints A3 compatíveis, relay WebSocket compartilhado dos sensores, terminal de pesquisa integrado com quatro áreas. PAPER/shadow, 24 candidatos e 90 minutos. Regras e parâmetros financeiros não modificados.

## Testes executados nesta entrega
**780 testes Python aprovados, sem skips**, com reexecução dos 640 herdados. A suíte A3 acrescenta 140 casos; parte adapta cenários existentes de integração a processos A3, não são 140 algoritmos inéditos.

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
| **Total** | **780** |

Logs integrais em `validation/*_tests.log`; resultados em `TEST_RESULTS_A3.json`.

### Regressão do arquivo ausente
Executado `a3.n2_worker` de produção em subprocesso Python real. O arquivo de amostras aparece somente depois de CAPTURING. Durante a espera o processo permanece vivo; depois registra previsões, labels válidos de 10s, updates e checkpoint completo. A janela acelerada dura cerca de 13 segundos. Backend/modelo da fixture e preços são sintéticos: **não é execução da GPU NVIDIA**, nem prova de qualidade preditiva em mercado. As janelas longas são verificadas nos testes temporais sintéticos herdados, não em uma captura real nesta entrega.

Testes adicionais: linha parcial/arquivo removido ou truncado, input inválido, forecast futuro/duplicado, vetor GPU inválido, final sem nova previsão, ausência de oportunidade distinta de crash, métricas contra taxa-base, pesos com hash errado/incompatíveis/futuros e isolamento do placar de avaliação.

### WebSocket e processos
Servidor upstream e clientes locais TCP reais, com frames/mensagens sintéticos. Doze clientes e duas ou mais assinaturas compartilham a única conexão upstream da fixture. Testadas autenticação recusada sem retry, Retry-After, 429 centralizado, recusa de método de escrita, frames/máscaras/ping/pong/fragmentos, consumidor lento, geração/ack, desconexão e invalidação local. Não é handshake TLS público nem feed Solana real. RAW permanece um recorder upstream separado; não se afirma uma única conexão total.

Executados paper_worker, UI e adaptadores em processos Python reais com IPC sintético. Encerramento/reinício da UI não altera a contabilidade; uma fonte inválida não produz operação; lacuna de um livro não impede o outro; replay dos outputs A3 coincide por hash. Não há build/executável Rust dentro destas fixtures.

### HTTP/SSE e navegador
Servidor de produção local testado com cliente HTTP/SSE Python, incluindo leitura de estado, Host/Origin, recusa de POST, limite de clientes, distribuição de estados recentes e finalização. O ambiente do Chromium bloqueou navegação tanto file:// como localhost com ERR_BLOCKED_BY_ADMINISTRATOR; o teste não contornou essa política. Assim, UI HTML/JavaScript foi exercitada com `set_content`, e servidor HTTP/SSE foi verificado separadamente. **Não foi executada uma navegação browser→HTTP→SSE end-to-end neste ambiente.**

**36 verificações de navegador aprovadas**: 1540, 1024 e 390 pixels de largura; quatro páginas, busca, seleção de ativo/grupo/política, atalhos, dados desconhecidos, estado de erro N2, tempo de 90m/45m, pausa/retomada sem fila velha, feed/heartbeat vencido e ausência de erro JS ou overflow horizontal externo.

Carga sintética de aproximadamente 100 mensagens/s alimentou o renderer. Foram contabilizados 322 eventos e 70 quadros; tempo de CPU do renderer p50 ~0,8 ms e p95 **2,5 ms**, máximo 3 ms, nesta janela curta local. **Não é latência blockchain→tela, nem benchmark do computador do usuário, nem teste de resistência de 90 minutos.** Resultados brutos em `BROWSER_A3.json`.

A interface reduz tráfego de apresentação e conserva DOM/canvas, sem frameworks/CDN/fontes externas. O alvo visual é até 25 Hz para livros e 4 Hz para painéis lentos, condicionado ao navegador e disponibilidade. Nenhuma promessa de “zero lag”.

## Preview e dados
A prévia utiliza somente snapshots reais arquivados da A2 do usuário, rotulados como **ARQUIVO A2, não captura A3**. N2 falha aparece como falha; não houve preenchimento inventado para deixá-la verde. Referências/hash em `PREVIEW_PROVENANCE.json`. O screenshot é captura do HTML implementado, não desenho conceitual.

## Preservação
157 arquivos A1/A2/N2/legacy/Rust preservados byte a byte conforme `docs/PRESERVATION_A3.json`. Os arquivos Rust em engine_a2, Cargo.toml e Cargo.lock não sofreram alterações. Campos novos de exibição de trailing/giveback não mudam os parâmetros econômicos. Testes comparam todos os campos econômicos originais; os JSONs A3 contêm metadados adicionais e não são declarados idênticos byte a byte aos A2.

## Limites nativos e de mercado
**Não existe cargo/rustc nem a GPU NVIDIA do usuário neste ambiente. Não houve compilação Rust A3, kernels GPU, conexão de mercado, RPC/WS público ou captura real de 90 minutos nesta entrega.** As evidências históricas A2 comprovam seus builds naquela sessão, não a integração nova. O executor mantém gates reais de Cargo por binário e autoteste GPU no computador de destino; falha não é convertida em sucesso por testes Python.

A N2 permanece consultiva e baseada no caminho spot amostrado. Previsão/label/update > 0 demonstram atividade do pipeline, não vantagem de negociação. Não se alteram entradas ou saídas para perseguir o resultado da amostra anterior. O número de cabeças/labels não equivale ao de negócios independentes. Memória só reutiliza gerações A3 compatíveis e completas, anteriores à nova sessão; estatísticas de avaliação reiniciam.

## Verificação do executor
O arquivo autocontido confere hash do payload, extração segura e hashes individuais, depois executa todas as dez suítes. O log desta execução será entregue **fora do payload selado**, para evitar hash circular. A opção --verificar-pacote não instala nem abre mercado.

## Operação
Release `releases/belial-a3-90m-v1`, runtime/modelos e evidências separados. Nove dezenas de minutos de captura após preparação; finalização e funding podem levar tempo adicional. Progresso explícito em terminal e painel. Sem ordens reais, sem carteira conectada, sem retomada de posições após crash. Resultado: `BELIAL_A3_90M_RESULTADO_*.zip` na pasta de instaladores.
