# Belial A2 — validação e limites / 90 minutos

## Entrega
CORE12 + EXPANSÃO12, fontes independentes, recuperação causal dos sensores, RPC/cache/cooldown compartilhados, coleta de snapshots on-chain destinada somente aos labels N2, funding e custos externos explícitos. Instalação isolada em `releases/belial-a2-90m-v1`; alvo nativo `target-belial-a2`; pesos novos em cada sessão A2. Não encerra uma A1 ativa, não substitui tokens por homônimos, não envia ordens reais.

## Testes efetivamente executados neste ambiente
**640 testes Python aprovados, sem skips.**

| Suíte | Testes |
|---|---:|
| MP12 / Tail / M2 herdados | 49 |
| N2 original | 22 |
| D2 | 4 |
| Runner / protocolos / FIX4 | 287 |
| Livros / PAPER original | 50 |
| A1 | 88 |
| A2: relay, recuperação, custos, snapshots, processos, contratos | 97 |
| N2 adaptada para 24 ativos | 22 |
| N2: labels e amostragem novos | 21 |
| Total | 640 |

Os 500 testes da A1 foram realmente reexecutados. Os 140 restantes incluem 22 adaptações da suíte N2 para 24 ativos; não se alega que todos são testes inteiramente inéditos. `validation/*_tests.log` e `TEST_RESULTS_A2.json` preservam os resultados.

Cobertura importante: concorrência/single-flight/cooldown 429 sem rajada após Retry-After, 401/403 sem contorno de acesso, falta de transação invalidando contexto e recuperação após 60 s, rejeição de contador/epoch regressivo, bloqueio de linhas anteriores à recuperação, CORE/EXP em carteiras independentes, realização de saída mesmo com sensor indisponível, replay sem duplicações/hash idêntico, custos desconhecidos como null, funding +76 ms, índices retidos mesmo após remoção da fila rápida, conflito de taxa torna o resultado desconhecido, e nenhum fill depois do deadline.

Os testes dos snapshots usam **contas sintéticas** nos layouts documentados. Conferem preço nos dois sentidos TOKEN/WSOL, mint/owner/decimais, AMM com OpenOrders/PnL, CPMM com taxas segregadas, CLMM sqrt, slot crescente, batches e proibição de swap/volume fictício. Não são uma captura de contas reais A2.

Os testes da N2 demonstram, em séries sintéticas, labels válidos nos horizontes de 300, 600, 900 e 1.800 segundos com observações suficientes e censura quando faltam dados. **Não provam que um RPC público manterá essa cobertura ao vivo.** A nova definição é CAMINHO OBSERVADO AMOSTRADO, não trajetória contínua: movimentos entre leituras podem não ser vistos.

## Processos locais e navegador
Executados processos Python de produção A2 com mensagens IPC sintéticas: encerramento/reinício da UI, uma fonte em quarentena, lacuna de um livro e continuidade do outro, bloqueio de PUMP, separação CORE/EXP e retomada de JTO com nova geração sem reinserir linhas antigas. Replay reproduziu os mesmos hashes dos registros PAPER nas fixtures. **São processos reais locais, não conexões reais às venues ou teste da GPU.**

Chromium local em 1540×1180 e 390×844: 24 cartões, troca entre grupos, valores all-in desconhecidos preservados, sem erro JavaScript e sem overflow horizontal externo. Cronômetro com relógio sintético: 01:30:00 / 0%, 00:45:00 / 50%, 00:00:00 / 100%. Sem preços ou lucros inventados para a prévia. `validation/BROWSER_A2.json` e imagens registram esses testes.

Todos os 120 arquivos Python foram analisados por AST; o JavaScript passou em `node --check`. Isso não substitui testes de tipos/compilação Rust.

## O que NÃO foi executado aqui
**Não há Cargo/rustc nem a GPU do usuário neste ambiente.** Não executei compilação/testes Rust da A2, kernels NVIDIA, RPC/WS público, resistência 90 min, nem preflight atual das 24 pools. O resultado recebido da A1 comprova build/testes nativos A1 no computador do usuário; NÃO comprova a nova alteração nativa A2.

Foram acrescentados 6 testes Rust de universo/identidade, além dos herdados/adaptados. `a2/build_worker.py` executa cargo test e cargo build offline/locked reais por binário antes de sua utilização. Reprovação nativa deixa o componente indisponível; não é aprovada pelo total de testes Python. O HTML mostra componente, tempo e últimas linhas; não há percentual de compilação inventado.

## Preservação e mudanças científicas
Os 81 arquivos em legacy continuam preservados/conferidos. A árvore operacional A1 e o engine A1 também foram mantidos, com documentação antiga em history_a1. Cargo.toml/lock, m2_core.rs, raydium_protocols.rs e token_programs.rs são byte a byte iguais em engine_a2. Regras e 32 cabeças foram comparadas.

Novo: contexto de ator é descartado após lacuna; retorno após 60 s sem falha, sem dados retroativos; somente eventos novos elegíveis. CORE/EXP possuem carteiras próprias. Labels da N2 ganharam amostras de estado; pesos A2 iniciam novos devido à mudança de contrato. Não se promete equivalência numérica nem superioridade à A1.

## Custos / funding
Taxas públicas/VWAP/latência continuam cenários. Funding usa taxa pública e índice observado; offset de até 1000 ms após a hora é associado à hora mantendo timestamp original. Índice ausente/taxa conflitante permanece desconhecido. Até 60 segundos finais servem somente à reconciliação, sem novos fills. Rede/bridge/saque/movimentações não são inventados: CONFIG_CUSTOS_EXTERNOS começa em null. All-in completa somente os itens explicitamente declarados quando funding/preço também conhecidos. Não é auditoria de todas as despesas reais possíveis. Custos externos não alteram o gate ou o capital reservado herdados; nova coluna de avaliação, sem mudança estratégica implícita.

## Operação
90 min após preparação; finalização/compactação podem levar tempo adicional. Sem retomada automática de posições. A seleção alternativa de RPC ocorre na preparação, não como rotação ao vivo. 24 candidatos não equivale a 24 qualificados. A disponibilidade real continua por ser verificada no computador do usuário, sem bloquear componentes independentes por um sensor.

As fontes públicas e os hashes dos arquivos de origem constam em docs/FONTES_A2.md e PROVENANCE.json. A verificação do próprio executor autocontido é entregue em log externo ao payload para evitar referência circular de hash.
