# Validação local — Belial A1 / 90 minutos

## Base e escopo

Implementação da revisão `BELIAL_A1_REVISAO_ARQUITETURA.md`, baseada no ZIP T3 90M FIX4 de SHA-256 `ecefad6d84e78791c4ec943574696b3cee7f96bd4427d2afcd6c2c0f346e8715`.

Escopo: supervisor local, disponibilidade por fonte, perps/UI independentes de mint Solana, captura raw separada, sensores isolados e RPC compartilhado, adaptação prospectiva de Tail/N2, contabilidade/replay e diagnóstico. TransferHook permanece bloqueado para decisões. Não se modificou a whitelist para fazer PUMP passar.

## Testes executados

**500 testes Python aprovados, sem skips.** Os logs completos e hashes estão em `validation/TEST_RESULTS_A1.json` e `validation/*_tests.log`.

| Grupo | Testes |
|---|---:|
| MP12 / Tail / M2 herdado | 49 |
| N2 herdado | 22 |
| D2 herdado | 4 |
| Runner / protocolos / FIX4 herdado | 287 |
| Livros e contabilidade herdados | 50 |
| A1: controller, fonte, relay, processos, replay e release | 88 |
| **Total** | **500** |

Os 412 testes herdados foram reexecutados, não apenas somados de relatórios antigos. A análise sintática dos **79 arquivos Python** passou. A análise JavaScript com `node --check` também passou.

### Casos importantes da A1

- TransferHook bloqueia PUMP; na fixture, os outros onze contratos são inspecionados sem exceção global. Aprovação estrutural da fixture não se confunde com mercado real ou qualificação nativa.
- Sem aprovação spot, o painel e os livros podem funcionar. Nenhuma fonte qualificada ou utilizável: não anunciar uma captura vazia de 90 minutos.
- Perda de fonte cancela intenção de entrada pendente; posições existentes e saídas pelo livro permanecem.
- Book Lighter com lacuna fica inválido, enquanto o livro Hyperliquid saudável permanece utilizável. Geração antiga e deltas repetidos não rejuvenescem uma cotação.
- Payload inválido é isolado na venue; falta de preço/funding não aparece como zero conhecido.
- Replay repetido não duplica sinal, fill ou funding. O hash dos outputs reconstruídos coincide com os outputs originais em cenários com abertura/saída.
- Cadeia adulterada, reordenada ou truncada é recusada.
- HTTP local do relay recusa métodos de escrita, Host/caminho incorretos e fila esgotada. Os retries compartilham a mesma limitação temporal. A falha upstream desses testes é simulada.
- Instalação/manifesto adulterado, arquivo extra ou symlink são recusados. Fontes e configurações preservadas são comparadas por hashes.

### Teste com processos reais locais

O teste `test_ui_crash_spot_quarantine_and_book_gap_do_not_stop_paper` executa o **paper_worker de produção** com dois pares IPC sintéticos em processos separados. Mata/reinicia o processo real da UI, publica uma falha de saúde spot, injeta lacuna de livro, verifica continuidade do outro livro, ausência de operação PUMP e igualdade de replay ao fim.

`test_live_adapter_duplicate_and_quarantined_source_do_not_reach_n2` executa o **analysis_worker de produção** em subprocesso, com arquivos de eventos sintéticos. Confirma ingestão de um swap válido, deduplicação, bloqueio após perda da fonte e ausência dos eventos recusados no arquivo agregado que alimenta a N2.

Esses são testes reais de processos Python, arquivos e HTTP loopback. **Não executam os binários Rust nem simulam uma interrupção real no provedor Solana**; as mensagens de mercado e estados de RPC são fixtures explicitamente identificadas. A GPU real não participa do segundo teste: verifica-se o arquivo de entrada, não kernels OpenCL.

### Painel

Chromium local, 1540 × 1180 e 390 × 844, sem rede de mercado. Foram verificados os doze ativos solicitados, ausência de erro JavaScript e ausência de overflow horizontal externo. Tabelas podem ter rolagem interna. Cronômetro: 01:30:00 no início, 00:45:00 aos 45 minutos e 00:00:00 aos 90 minutos, com relógio sintético. Nenhum preço, operação ou lucro foi inventado para a prévia.

Resultados e imagens: `validation/BROWSER_A1.json` e `validation/dashboard_a1_*.png`.

## Preservação e alterações

81 arquivos da base permanecem byte a byte em `legacy/`, inclusive políticas, modelos, configuração N2 e contabilidade original. Os hashes são rechecados em testes.

Os arquivos operacionais novos em `a1/` adaptam ciclo de vida, disponibilidade e registro. A cópia operacional `engine/` altera `main.rs` para escopo de uma fonte/heartbeat e o transporte perp para recusa explícita de acesso; acrescenta recorder raw. `Cargo.toml`, `Cargo.lock`, `m2_core.rs`, `raydium_protocols.rs` e `token_programs.rs` foram preservados. Reorganizar a disponibilidade dos dados altera o experimento: não se afirma equivalência numérica automática com o T2/T3.

## Limite nativo — não apresentado como aprovado

**Não há Cargo/rustc nem a GPU NVIDIA do usuário disponíveis neste ambiente. Não executei compilação Rust, testes Rust, rede RPC/WS pública, medição de latência da conexão do usuário ou captura real de 90 minutos.**

A exigência da revisão de evidência nativa completa ainda não foi atendida aqui. Foram incluídos oito novos testes de escopo Rust e cinco do recorder raw, além dos testes nativos herdados. O instalador roda `cargo test` e `cargo build` reais, isolados por binário, antes de permitir sua utilização. Código não compilado não é tratado como fonte qualificada. Erro em um build aparece no painel sem ser ignorado para liberar o componente.

O pacote é uma primeira migração implementada com prova de isolamento local e gates nativos pendentes no computador de destino; não é prova de integração completa dos feeds de mercado nem de rentabilidade. A disponibilidade atual dos doze tokens/perps e o suporte aos casos reais continuam sendo verificados durante a preparação e captura.

## Executor autocontido

O `.sh` verifica hash do payload, extração segura, membros/hashes individuais e as seis suítes Python. A opção `--verificar-pacote` não instala, não usa mercado, não envia ordens e não declara validação Rust/GPU. O log de execução do próprio `.sh` é fornecido junto da entrega, separado do payload selado para evitar hash recursivo.

Não existe retomada automática do PAPER depois de crash ou perda de energia. O objetivo desta etapa é conter falhas, preservar a evidência e recusar decisões sem dados — não ocultar interrupções.
