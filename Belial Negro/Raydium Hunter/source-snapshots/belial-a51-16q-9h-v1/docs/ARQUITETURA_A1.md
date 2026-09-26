# Arquitetura implementada — A1

## Fluxo

```text
supervisor
 ├─ UI: consumidor de estados, HTTP/SSE local
 ├─ build_worker: gates nativos por binário
 ├─ PAPER: registro e contabilidade
 │   ├─ venue_worker Hyperliquid → transporte Rust
 │   └─ venue_worker Lighter     → transporte Rust
 ├─ spot_prepare: descoberta/contratos/qualificação por ativo
 ├─ raw_worker → recorder Rust de logs públicos
 ├─ RPC relay local: métodos de leitura e orçamento compartilhado
 ├─ sensor_worker por ativo qualificado → coletor Rust isolado
 ├─ analysis_worker → Tail principal e shadow preservados
 └─ n2_worker → OpenCL preservado, processo independente
```

A independência é de disponibilidade e ciclo de vida, não de recursos físicos: CPU, SSD e conexão continuam compartilhados. A1 não faz instalação de Kafka/Kubernetes nem depende de servidor pago.

## Dependências

UI inicia primeiro. Cada binário só é considerado disponível se `cargo test --bin ...` e `cargo build --bin ...` retornarem zero; um binário antigo meramente existente no target não aprova a etapa. Perp é construído primeiro, raw depois, spot depois. Compilação offline usa o lockfile preservado. Falha de build de spot não derruba o PAPER nem sua outra venue.

Preparação spot compara identidades por mint e protocolos; cada incompatibilidade vira capacidade local. Uma conta com TransferHook não ganha exceção. Seu log pode ser registrado com `raw_only=true`, `price_validated=false`, `signal=false`. Esses arquivos não são entrada do Tail ou da N2.

A qualificação dos novos formatos compara eventos históricos aceitos pelo Rust com interpretação independente Python. Isso não treina a N2 nem abre posições retroativas. O AMM v4 mantém o leitor herdado e a verificação nativa estrutural; seu rótulo de qualificação não afirma um novo smoke test de transação histórica.

## Isolamento de RPC

Coleta spot passa por um relay loopback com caminho aleatório e verificação de Host. Whitelist de métodos públicos de leitura; payloads, fila e duração de chamadas limitados. O mesmo orçamento de 3 tentativas HTTP por segundo é aplicado a todas as fontes e aos retries. Uma recusa 401/403 paralisa esse upstream spot, não contorna o bloqueio e não derruba perps. Não se escolhe um novo provedor no meio do experimento.

O orçamento cobre as requisições HTTP dos sensores em captura. Descoberta/qualificação acontecem na preparação. Subscrições WebSocket e a ingestão raw não são requisições HTTP RPC desse orçamento.

## Integridade e disponibilidade temporal

A1 mantém o timestamp original de recepção/disponibilidade do ator e acrescenta `a1_available_ms`. O Tail decide no instante de processamento, não no passado. Deduplicação dos eventos agregados usa SQLite com chave de origem/ativo/evento; rotação/truncamento de fonte são falhas explícitas. Inputs de fontes em quarentena não são enviados à N2.

Um heartbeat Python não prova saúde do websocket: o wrapper exige ACK nativo, atualização de rede/pong e estado nativo recente, sem erro RPC ou descarte registrado. Após perda/reconexão/erro, esse sensor fica em quarentena pelo restante do cohort. Não existe retomada silenciosa de sinais. Raw e livros podem continuar/reconectar em suas próprias gerações.

A lista qualificada é congelada no início dos 5.400 segundos. N2 mantém seus horizontes, no máximo 1.800 segundos, e janela incompleta fica censurada. Falha da GPU deixa N2 indisponível; não são mostradas previsões novas fictícias. A lógica original não exigia N2 como gate de entrada; A1 não muda isso.

## Livros, contabilidade e falhas

Cada venue tem processo e fila. Lacuna de nonce, geração antiga, payload inválido, livro vencido ou pressão excessiva na fila invalidam a fonte correspondente. Não se descarta delta necessário silenciosamente para aumentar velocidade aparente.

A UI recebe snapshots agregados em alvo de 25 Hz; recepção/processamento de mensagens é independente desse desenho da tela. A taxa efetiva e os atrasos são medidos, não prometidos. Armazenamento e contabilidade são compartilhados dentro do processo PAPER: falha fatal de gravação não permite alegar continuidade auditada.

O Controller cancela somente novas intenções de ENTRY cujos pré-requisitos falharam. Não elimina posições abertas. Saídas usam a mesma venue e seu livro utilizável; sem preço de saída, posição fica aberta/censurada. O preço spot de referência não se transforma em fill perp.

O PAPER não é reiniciado automaticamente depois de falha fatal: reiniciar com livro-caixa vazio seria perda de estado. Outros componentes continuam e o painel marca falha/integridade. Recuperação transacional completa pós-crash/power loss não faz parte desta primeira migração.

## Journals e replay

`paper/inputs.jsonl` e `paper/outputs.jsonl` possuem sequência e cadeia SHA-256, com flush e fsync periódicos. O replay consome inputs verificados em ordem e reconstrói o Controller sem rede, ordens reais ou treinamento N2. Ao finalizar, compara o hash integral dos outputs reconstruídos com o registro original. Uma lacuna/truncamento/adulteração é recusada.

Isso verifica determinismo e reconciliação dos dados gravados; não prova fills reais, completude dos eventos que nunca chegaram ou continuidade de mercado durante toda a sessão. Cobertura no SUMMARY é amostragem do supervisor, identificada como tal. `experiment_integral_certified` permanece falso: qualificação inicial sozinha não certifica 12/12 contínuo.

## Fontes técnicas de protocolo

Referências oficiais conferidas na revisão; documentação não é evidência de mercado desta execução:

- Solana logsSubscribe: https://solana.com/docs/rpc/websocket/logssubscribe
- Solana getTransaction: https://solana.com/docs/rpc/http/gettransaction
- Solana Token-2022 / hook: https://solana.com/docs/tokens/extensions/transfer-hook
- Hyperliquid subscriptions: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions
- Lighter WebSocket: https://apidocs.lighter.xyz/docs/websocket-reference
