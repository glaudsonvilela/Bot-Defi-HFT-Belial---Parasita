# Belial A7 — 20 contratos meme, sinais Solana/BNB, duas horas PAPER

## Correção FIX3 — RPC BNB e diagnóstico

A sessão `m2-a7-20260927T011607118289Z` registrou 19 erros RPC_-32005 na BNB e nunca iniciou a captura. O endpoint BNB dataseed escolhido desabilita eth_getLogs. O padrão passa a PublicNode BSC, com preflight real de logs antes da preparação; não há failover automático. RPC customizado explícito continua tendo prioridade. CONNECTED BNB requer consulta de logs bem-sucedida. Prontidão requer livros atuais, ambos os sensores recentes e os três canais Binance conectados. O limite de 150 s permanece.

O painel passa a mostrar pendências por feed e tabela de operações com PnL aberto/realizado; PnL aberto sem livro válido é desconhecido. Nenhuma regra econômica foi alterada. Veja `docs/ANALISE_A7_FIX3.md` para contagens, fontes e limites da validação. O novo endpoint foi consultado com sucesso durante a preparação desta entrega; o teste real de duas horas no PC continua pendente.

Checkpoint anterior informado pelo usuário: `cd8aa4e513c68edad65cdaf8d1f98bad384e1204`, branch `work/belial-a7-2h-0f7ac753593d`. FIX3 usa release separada e novo checkpoint; não apaga as anteriores.

## Correção FIX2 — inicialização Solana

O A7 original trazia um hash mainnet incorreto (`…dw2x`). O valor correto, confirmado no SDK oficial e no coletor legado do próprio projeto, é `5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d`. A checagem estrita de rede permanece ativa. Agora o teste usa uma resposta de referência independente da constante de produção, aceita mainnet e recusa devnet/testnet, valor antigo e resposta inválida. `NETWORK_CHECK_SOLANA.json` registra valor esperado/recebido sem armazenar URL ou credenciais.

A tentativa em 2026-09-27T01:07:39Z terminou antes da captura; o serviço transitório foi recolhido pelo systemd. A revisão FIX2 instala em diretório novo e preserva a release e o ZIP anteriores. Mantém FIX1 do hash Git, sinal, saída, custos, 20 contratos e duas horas PAPER. Após falha, imprime `A7_RELATORIO_LOCAL` e tenta abrir o HTML estático com o motivo; o link HTTP antigo não permanece ativo. Não há afirmação de teste real concluído.

Checkpoint A7 anterior informado pelo usuário: `65c548378b75c638a17a401aebd65c8cf7f80b07`, branch `work/belial-a7-2h-f03119da3933`. FIX2 gera novo checkpoint com todos os arquivos.

## Estado da entrega

Release separada `belial-a7-crosschain-2h-v1-fix3`, baseada no pacote A6 completo e no checkpoint Git `31040a0ae01ac93597095fa9af6c73772e5fa60c`. Nenhuma ordem real; não exige credenciais Binance. A captura de duas horas ainda não foi executada no ambiente de geração. Cargo e GPU não estavam disponíveis aqui. A compilação e os testes Rust offline são obrigatórios no PC antes da captura; um erro aborta e fica nos logs.

O instalador destina-se a `/home/hobit/Downloads`. Instala no SSD já usado pelo projeto, preserva releases anteriores e espera A51R/A6 terminar. Não encerra o scan anterior. Se ele estiver travado, será necessário encerrá-lo conscientemente antes de iniciar a captura A7. O serviço continua após fechar o terminal. `--verify-only` valida pacote/testes sem instalar; `--install-only` instala e faz checkpoint sem agendar captura. Executar sem sudo.

## Universo e liquidez

“20 pools da Binance” é operacionalizado como 20 contratos perpétuos USDT de memecoins, pois os mercados centralizados são livros, não pools DEX. `a7/candidates.json` contém 26 correspondências explícitas entre contrato, ID CoinGecko e endereço canônico. Não há associação automática por ticker. Os multiplicadores 1000/1000000 são aplicados ao estimar o valor da venda on-chain.

`research/PRELIMINARY20.json` mostra a consulta real de livros Binance feita na preparação desta entrega. É uma pré-seleção, não um atestado de pools ativas. A inicialização reconsulta status TRADING, lotes, volume, livro, endereços e pools. Ordena pela menor profundidade dos dois lados dentro de 10 bps do preço médio, com desempate por volume 24h. Filtros comuns: volume ≥500 mil USDT/24h, profundidade mínima de 100 USDT por lado nessa faixa, spread ≤15 bps, custo modelado de ida/volta de 100 USDT ≤30 bps. Profundidade acima de 1.000 USDT ocorreu em apenas parte das candidatas consultadas; não afirmamos que todas as 20 passam esse nível. O lote efetivamente simulado costuma ser menor e é revalidado no livro de cada entrada.

Uma pool on-chain precisa ter endereço e quote compatíveis, liquidez informada ≥50 mil USD e passar a checagem RPC. Na Solana aceitamos SPL tradicional em Raydium AMM v4/CPMM/CLMM ou PumpSwap: proprietário do programa e presença dos mints na conta são verificados; isso não é uma auditoria completa do layout/protocolo. Na BNB aceitamos PancakeSwap V2/V3, checando factory, token0/token1, getPair/getPool e decimais. Token-2022, Pancake Infinity e outros protocolos ficam fora desta versão.

O programa registra a cobertura real de cada uma das 20 moedas. Uma moeda pode existir apenas em uma rede ou não ter pool elegível nas duas redes observadas. Sem pool verificada, a nova regra de entrada fica bloqueada nela; o controle ainda observa seu contrato Binance. O teste exige pelo menos uma pool confirmada em cada rede e todos os 20 livros Binance atuais antes de iniciar o relógio. Se não houver 20 contratos elegíveis ou cobertura das duas redes, aborta e explica por quê. Não inventa endereços nem baixa filtros para completar a lista.

As consultas DEX não puderam ser concluídas no ambiente de geração. Portanto, a lista de 20 pools on-chain NÃO está previamente validada; a validação ocorre no PC e pode impedir o início. URLs/API retornando 401/403/451 são bloqueadas, sem contorno nem rotação de provedor.

## Quatro carteiras simultâneas

- CONTROLE: regras de sinal e saída A6; queda 30/120 s, fluxo vendedor, custo, stop/trailing originais.
- ENTRADA: controle + duas reduções líquidas observadas da mesma wallet em 60 s, valor estimado agregado ≥500 USDT, último evento disponível há no máximo 15 s, confirmação vendedora/preço de 10 s e exclusão de quedas já estendidas além de 250 bps em 120 s.
- SAIDA: entrada do controle + saída por perda da tese: após 30 s de posição, prejuízo bruto ≥10 bps e fluxo vendedor de 10 s ≤45% mantido por 5 s. Stop/trailing mantêm prioridade; execução exige novo livro após 300 ms.
- COMBINADO: nova entrada e nova saída juntas.

Todas usam capital PAPER independente de 1.000 USDT, até oito posições, dimensionamento comum visando 1 USDT de risco por posição, teto de 100 USDT nocionais, custos/volatilidade e ordenação causal comum. Stops e latência não garantem perda máxima de 1 USDT. Como dimensionamento e ordenação mudam para todos os braços, CONTROLE NÃO reproduz literalmente a carteira A6 antiga. Comparações principais são entre braços simultâneos; a comparação com a sessão antiga é apenas descritiva.

Taxa presumida 5 bps por lado, spread e caminhada real no top20; reserva adicional de 3 bps para gate/dimensionamento. Esses 3 bps não são debitados como comissão no PnL: o fill usa o livro posterior à latência. Não se afirma tarifa real da conta nem execução real. Funding previsto não vira lucro: depois da captura buscamos o histórico público de liquidação e calculamos fluxo do short com markPrice daquele evento. Falta de informação gera UNKNOWN, nunca zero presumido. Custos pessoais externos permanecem fora do modelo.

## Coleta, atribuição e ruído

Corrigida a desconexão do relay legado por silêncio de entrada de 50 s, que podia interromper clientes ainda recebendo dados. `maxSupportedTransactionVersion` passa de 0 a 1 no preparador e no resolvedor Rust de Raydium. O novo coletor A7 não depende do relay legado: usa um WebSocket Solana com heartbeat e assinatura por pool.

Solana: transação confirmada, assinatura, programa/pool presentes e invocação verificados. Uma wallet signatária precisa ter redução líquida do token e aumento líquido da quote. Contas ausentes/criadas/fechadas sem ambos os saldos são UNKNOWN. Uma transação contendo a pool não prova que a wallet executou a instrução específica da pool. A atribuição é deliberadamente rotulada como descarte líquido no contexto da transação. O fechamento de WSOL pode excluir vendas válidas. Dados confirmados ainda podem sofrer reorganização; esta versão não reconcilia todos os eventos Solana com finalidade posterior.

BNB: apenas blocos finalized, âncora verificada, receipt bem-sucedido e log correspondente. ABI Pancake V3 usa sete palavras não indexadas, incluindo protocolFeesToken0/1; o evento difere do Uniswap V3. Exigimos entrada do token na pool e fluxo líquido de Transfer negativo para o remetente da transação. Eventos de Transfer não equivalem a leitura completa de saldo, e transferências fora do swap podem contaminar a atribuição. Não cobre perfeitamente rebasing/taxas não padronizadas.

Isso identifica uma proxy observável de venda líquida recorrente; não identifica uma conta HFT dentro da Binance. Não identifica a pessoa controladora nem comprova que a wallet seja lucrativa. Transações on-chain, assinaturas/receipts e disponibilidade temporal são arquivadas para auditoria.

Há amostragem limitada pelo RPC: um trabalho pendente por pool Solana, dois resolvedores, cache limitado, três chamadas/s; BNB amostra logs recentes, uma transação por pool em cada lote e orçamento temporal de seis segundos. Descartes, atrasos, gaps, duplicatas e UNKNOWN aparecem nos contadores. Atrasos acima de 15 s não alimentam a nova entrada. Não representa observação integral de todos os swaps. Chamadas bloqueadas não são substituídas por outro provedor automaticamente.

## Recursos e proteção da captura

O feed Rust usa o paralelismo disponível do runtime, sem cota artificial de CPU/RAM no serviço. A compilação escolhe jobs pela RAM disponível e pelo número de CPUs. Filas, caches e evidência têm limites concretos para impedir que backlog vire um sinal atrasado. Guarda inicial de 8 GiB livres; interrompe antes de consumir a reserva de 1 GiB. CPU/RSS/memória/pressão são amostradas a cada cinco segundos. Mais utilização por si só não melhora a estratégia.

A GPU/N2 anterior permanece no snapshot histórico, mas não é treinada nem usada neste teste: o alvo antigo TOKEN/WSOL não foi validado para shorts Binance guiados pelas novas features. Não há trabalho GPU sintético para produzir percentual de utilização. O gargalo on-chain tende a ser latência/cota do RPC. Para ampliar coleta útil, medir essa limitação primeiro.

Endpoints públicos padrão: Solana mainnet-beta e BNB `bsc-rpc.publicnode.com` (FIX3). Se você já tem RPC autorizado, pode exportar `BELIAL_SOLANA_RPC_URL`, `BELIAL_SOLANA_WS_URL`, `BELIAL_BNB_RPC_URL` antes de executar. Não colocar chaves no código. Não registrar URLs com chaves no relatório. Solana genesis e chainId 56 são verificados. Alguns RPCs públicos podem recusar logs, finalized ou o volume necessário; o teste registra ou aborta conforme a etapa.

## Evidência e análise

`inputs.jsonl` e `decisions.jsonl` têm sequência e cadeia SHA-256. O replay repete a disponibilidade dos eventos na ordem original e compara cada decisão e o estado final, sem olhar dados futuros. Transações brutas ficam em `CHAIN_EVIDENCE.jsonl.gz`. `UNIVERSE.json` congela seleção, endereços e cobertura; `COMPARISON.json`/`COMPARACAO.md` comparam número de entradas, stops, MFE ≥100 bps, expectativa, exposição nocional e tempo posicionado; `FUNDING_RECONCILIATION.json` separa funding conhecido/desconhecido. `RELATORIO.html` é um painel estático que abre sem rede. `A7_SUMMARY.json` declara duração, falhas, amostragem e limites.

Posições abertas ao fim são censuradas, sem fechamento inventado. Resultados com lacuna de livro carregam `path_censored`; não os tratar como trajetórias completas. Zero trades, pouca cobertura on-chain ou duas horas de evento raro não permitem concluir ausência/presença de vantagem. Nenhum ajuste automático de parâmetros usa o resultado desta mesma janela. Repetir em janelas independentes antes de qualquer conclusão de lucro.

O instalador cria checkpoint em branch própria, registra hashes dos blobs staged e tenta sincronizar essa branch. Não altera nem mescla main. Manifests A6/A51 incluídos são históricos; para esta release vale `A7_MANIFEST_SHA256.json`. O build nativo e a captura real estão marcados como pendentes até execução no PC.

## Execução

```bash
bash /home/hobit/Downloads/BELIAL_A7_CROSSCHAIN_2H_INSTALAR.sh
```

Acompanhar: `journalctl --user -u belial-a7-2h.service -f`.
Parar de forma controlada: `systemctl --user stop belial-a7-2h.service`.
O relatório será `/home/hobit/Downloads/BELIAL_A7_2H_RESULTADO_*.zip`.

## Fontes de implementação

- Binance USD-M Futures Market Data/WebSocket: https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams
- Binance funding history: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History
- Solana getTransaction: https://solana.com/docs/rpc/http/gettransaction
- Solana logsSubscribe: https://solana.com/docs/rpc/websocket/logssubscribe
- CoinGecko coins/list (include_platform): https://docs.coingecko.com/reference/coins-list
- DEX Screener API: https://docs.dexscreener.com/api/reference
- Pancake factories: https://developer.pancakeswap.finance/contracts/v2/addresses ; https://developer.pancakeswap.finance/contracts/v3/addresses
- PumpSwap IDL: https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump_amm.json
