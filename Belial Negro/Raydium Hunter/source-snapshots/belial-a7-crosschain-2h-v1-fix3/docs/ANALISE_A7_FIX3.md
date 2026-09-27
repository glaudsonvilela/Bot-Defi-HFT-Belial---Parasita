# Belial A7 — análise da falha e correção FIX3

Arquivo analisado: `BELIAL_A7_2H_RESULTADO_20260927T011607118289Z.zip`.
Sessão: `m2-a7-20260927T011607118289Z`.

## Resultado

A captura de duas horas **não começou**: `elapsed_ms=0`, nenhuma entrada e nenhum resultado financeiro a avaliar. A preparação chegou aos 20 contratos; o bloqueio ocorreu na confirmação dos feeds.

| Evidência | Resultado |
|---|---:|
| Arquivos conferidos no manifesto | 72, sem divergências |
| Eventos de entrada verificados | 12.612 |
| Decisões reproduzidas pelo replay | 12, idênticas; eventos de infraestrutura, não trades |
| Atualizações de livros Binance | 8.332 |
| Buckets de fluxo recebidos / aceitos | 579 / 570 |
| Maior número simultâneo de livros recentes | 20 |
| Livros recentes no estado final | 15 |
| Conexão Solana | Confirmada, 28 heartbeats |
| Atribuições de transações Solana | 38 |
| Vendas líquidas Solana qualificadas | 2 |
| Erros BNB `RPC_-32005` | 19 |
| Lacunas posteriores de faixa BNB | 9 |
| Pools verificadas na cesta | 11 Solana + 7 BNB |
| Moedas sem pool elegível | TURBO e MELANIA |

Os três canais Binance — preço/funding, profundidade e fluxo — conectaram. A Solana também conectou e resolveu transações. Houve uma ocorrência de HTTP 429 na resolução Solana, registrada como erro de amostra; isso não foi o bloqueio persistente.

## Causa

O A7 usava `https://bsc-dataseed.bnbchain.org` para ler logs de swaps. A documentação oficial da BNB informa que **eth_getLogs está desabilitado nesse endpoint** e orienta utilizar um provedor compatível.

O coletor marcava a BNB como CONNECTED após ler o bloco finalizado, antes de demonstrar que conseguia ler logs. Logo depois passou a GAP com `RPC_-32005`. Enquanto ambas as redes apareceram CONNECTED, havia no máximo 14 livros recentes; quando os 20 ficaram recentes, a BNB já estava em GAP. A condição de início nunca foi satisfeita.

O log antigo registrava apenas o código RPC, sem o nome do método ou a classificação da resposta. Portanto, não permite atribuir isoladamente cada um dos 19 erros a um método. A incompatibilidade do endpoint com o método exigido pelo coletor é confirmada pela configuração, pelo código e pela documentação do serviço; os eventos observados são consistentes com ela.

## FIX3

1. Endpoint BNB padrão passa a `https://bsc-rpc.publicnode.com`, um serviço público da PublicNode. Um endpoint customizado em `BELIAL_BNB_RPC_URL` continua tendo prioridade. Não há rotação automática por erro ou contorno de recusas de acesso.
2. Antes de compilar/selecionar, o preflight verifica chainId 56, bloco finalizado e **uma consulta eth_getLogs filtrada de um único bloco**. Uma lista vazia comprova capacidade de consulta, não presença de vendas. Resultado fica em `NETWORK_CHECK_BNB.json`.
3. BNB só informa CONNECTED após uma leitura bem-sucedida de logs. Método indisponível gera falha explícita; restrições RPC repetidas encerram o coletor com o motivo, em vez de manter um falso estado saudável.
4. Erros incluem método, código e categoria, sem arquivar mensagens que possam conter URL/chaves.
5. Painel e terminal mostram livros ausentes/desatualizados, estado de cada canal e rede, e motivo da espera. `READINESS.json` preserva esse diagnóstico.
6. Mantidos os 150 segundos, 20 livros com no máximo 2 segundos de idade e cobertura das duas redes. A prontidão também exige heartbeat recente e os três canais Binance conectados.
7. Painel passa a listar posições abertas e até 50 fechadas recentes por carteira, com entrada, saída, PnL, stop e trailing. PnL aberto usa cotação atual e desaparece se o livro ficar inválido/desatualizado; não é lucro realizado. Funding continua separado.

Sinal, dimensionamento, stop, trailing, regras de entrada/saída, quatro carteiras e duração de duas horas não foram alterados nesta correção. Os dados recebidos na preparação continuam sendo preservados; nenhuma operação é criada antes do início confirmado.

## Validação

- O novo endpoint respondeu chainId `0x38`, bloco finalizado e eth_getLogs com filtro das sete pools BNB da sessão. Essa consulta devolveu zero logs no bloco consultado; não foi tratada como um evento de venda.
- Uma verificação curta, somente de leitura, com o coletor BNB atualizado alcançou CONNECTED após consultar logs. Nenhum swap foi observado nesse teste curto. Isso não substitui a captura de duas horas no PC nem garante disponibilidade contínua do serviço público.
- Os 12.612 eventos reais do ZIP foram reproduzidos pelo código atualizado: mesmas 12 decisões e mesmo hash final `1840dd8aafe4db9e3287234fae2e750884df827c3371f8cd41f8253026597836`.
- Testes novos cobrem RPC com método desabilitado, preflight de logs, ausência de falso CONNECTED, diagnóstico por feed e PnL indisponível em livro antigo. Há verificações JavaScript de exibição das falhas e das operações.
- A checagem de mainnet Solana e a proteção entre manifesto/hash Git das correções anteriores permanecem.
- Rust não foi alterado nesta correção. Compilação/testes nativos continuam sendo executados no PC antes da captura.

A série miniTicker foi esparsa nesta preparação (522 eventos recebidos); portanto, algumas moedas podem permanecer em aquecimento ou ficar sem sinal mesmo com o feed conectado. Essa observação não foi usada para alterar o modelo de preço ou relaxar os filtros. O relatório da captura deve distinguir falta de oportunidade, aquecimento, falta de cobertura e falha de dados.

## Execução

Baixe novamente o instalador, substituindo a cópia anterior em Downloads, e execute sem sudo:

```bash
bash /home/hobit/Downloads/BELIAL_A7_CROSSCHAIN_2H_INSTALAR.sh
```

A primeira identificação será `A7_INSTALLER_FIX3`. A nova release é `belial-a7-crosschain-2h-v1-fix3`; versões e evidências anteriores ficam preservadas. O instalador cria um novo checkpoint Git e agenda o serviço PAPER.

Procure `A7_BNB_LOGS_PREFLIGHT=PASS`, `A7_FEEDS=...` e, por fim, `A7_CAPTURA_2H_INICIADA=`. O endereço do painel será novo.

## Fontes

- BNB Chain, restrições dos endpoints: https://docs.bnbchain.org/bnb-smart-chain/developers/json_rpc/json-rpc-endpoint/
- PublicNode, endpoint público BSC: https://bsc.publicnode.com/
