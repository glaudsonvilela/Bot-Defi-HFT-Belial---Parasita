# Belial A2 — 90 minutos / CORE12 + EXPANSÃO12

## Executar
Salve o executor em `/home/hobit/Área de trabalho/Altside` e execute **sem sudo**:

```bash
bash "$HOME/Área de trabalho/Altside/BELIAL_A2_90M_EXECUTAR.sh"
```

É uma sessão nova. Releases, evidências e pesos antigos não são sobrescritos. Outro supervisor A1 ou coletor ativo causa recusa, não encerramento. O dashboard abre durante a preparação. A captura dura 90 minutos depois dos preflights; compilação e finalização ficam fora do período. Ao terminar, `RESULT_ZIP=` indica `BELIAL_A2_90M_RESULTADO_*.zip` na mesma pasta. Mantenha terminal aberto, SSD montado, computador ligado/sem suspensão. Não há reinício automático de posições após queda de energia.

Modo de verificação offline, sem instalar/iniciar mercado:

```bash
bash "$HOME/Área de trabalho/Altside/BELIAL_A2_90M_EXECUTAR.sh" --verificar-pacote
```

## Escopo
CORE12 preserva FARTCOIN, BOME, WIF, POPCAT, RAY, JUP, BONK, PENGU, PUMP, USELESS, GRASS, PYTH.
EXPANSÃO12: JTO, RENDER, W, TNSR, DRIFT, TRUMP, MELANIA, ORCA, HNT, MEW, MPLX, KMNO.
Identidades e referências estão em `UNIVERSE_A2.json`. São candidatos de pesquisa, não certificação de 24 pools ativas/listagens atuais nem recomendação de investimento. Descoberta Raydium AMM v4/CPMM/CLMM TOKEN/SOL e qualificação real permanecem obrigatórias por fonte. Um ativo ausente/incompatível fica explícito; não é substituído automaticamente.

Estratégia principal de referência preservada; episódios shadow CORE e EXPANSÃO geram simulações independentes. Cada grupo, política e venue possui carteira própria. Não somar os seus retornos como capital único. USD100 por entrada/1000 por carteira são parâmetros PAPER, não capital real.

## RPC e recuperação
Novo relay local com no máximo 3 tentativas HTTP/s, limite global compartilhado, desaceleração após 429, respeito a Retry-After, deadline/fila/cache limitados e single-flight por método/parâmetros. Sucesso de getTransaction pode ser reutilizado por 30s; resposta nula fica no cache somente 350ms; getMultipleAccounts NÃO usa cache. 401/403 interrompem aquele upstream; não há rotação de IPs nem alternância de provedor para contornar acesso. A seleção/fallback de provedor é na preparação, não ao vivo.

Falha transitória invalida o contexto do ator, não encerra permanentemente o sensor. Reinicia a janela de contexto, descarta linhas antigas e exige 60s de recuperação sem nova falha antes de admitir sinais. Dados raw e livros continuam separados. Erro nativo/IO/regressão de contador segue como quarentena. Posições existentes não desaparecem: saídas dependem do livro, não do retorno do sensor.

## N2: correção com medição adicional
Mantidos 8192 features ×32 cabeças e seus thresholds/horizontes. Novo label revision: caminho OBSERVADO de swaps + snapshots on-chain TOKEN/SOL, não trajetória contínua, nem retorno perp líquido. Leituras somente para labels, alvo a cada 10s, máximo30s entre observações. Elas não geram swaps, volume, wallets, features ou sinais. Cada pool e suas dependências são lidas num mesmo lote/slot; mints, owner, decimais e avanço do slot são revalidados. AMM inclui open-orders/PnL, CPMM subtrai taxas segregadas, CLMM usa sqrt_price.

A ausência de swaps NÃO é preenchida repetindo a última cotação. Sem snapshot válido também não há preço inventado: janela com gap excessivo/incompleta é censurada. As amostras podem perder movimentos entre leituras; labels válidos se referem ao alvo amostrado, não a toda a trajetória. RPC insuficiente ainda pode impedir labels longos. Pesos A2 iniciam separados/frescos porque o contrato mudou; os antigos não são reinterpretados nem apagados. N2 continua consultiva e não alimenta o signal engine.

## Funding e custos
Funding público + índice efetivamente observado formam um MODELO, não extrato de conta. Índices próximos da hora ficam preservados fora da fila rápida. Histórico HL com offset até1000ms após a hora é associado àquela hora, mantendo o timestamp original; conflito ou falta de índice/taxa continua desconhecido. Após o fim da captura há até60s de reconciliação de funding, sem novos livros usados para fills, sinais ou prolongamento da janela. Falta de evidência não é substituída por zero.

O painel separa ex-funding, líquido do modelo e all-in DECLARADO. Spread/impacto já estão no VWAP; não são cobrados outra vez. Custos externos começam como `null`, inclusive gas aplicável, movimentações alocadas e outros. Não é possível certificar todos os gastos de uma conta real que não existe nesta simulação. Não há gas spot Solana por entrada porque spot só é sensor. Custos de redes/depósito/bridge/saque devem refletir a operação efetiva, não uma taxa inventada por trade.

Para um cenário externo, copie `CONFIG_CUSTOS_EXTERNOS.json` para fora da release, preencha todos os valores aplicáveis por venue (USD por trade, movimentos previamente amortizados), escolha `USER_ASSUMPTIONS` ou `DOCUMENTED_SCENARIO` e descreva a origem. Zero é valor explicitamente declarado, não fallback. Antes de uma NOVA sessão:

```bash
BELIAL_A2_COSTS_FILE="$HOME/Área de trabalho/Altside/BELIAL_A2_CONFIG_CUSTOS_EXTERNOS.json" \
  bash "$HOME/Área de trabalho/Altside/BELIAL_A2_90M_EXECUTAR.sh"
```

O perfil é congelado em COSTS_FROZEN.json. Custos externos alteram a apresentação do resultado all-in, NÃO o gate herdado nem seu dimensionamento de capital nesta etapa. Não há modelo maker-queue, margem alavancada/liquidação, nem execução real. 'All-in completo' significa todos os ITENS DECLARADOS conhecidos, não auditoria de despesas reais desconhecidas.

## Validação
Consulte `VALIDACAO_LOCAL.md` e `validation/`. Testes locais Python/processos/Chromium são separados de fixtures, dados A1 e nativos. Cargo, GPU e acesso de mercado A2 não foram executados neste ambiente. Cada componente nativo continua bloqueado até testes/build reais no computador do usuário; falha de um componente não autoriza ignorar a validação nem derrubar os outros.
