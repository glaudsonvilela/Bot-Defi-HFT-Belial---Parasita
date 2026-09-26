# Belial A6 Wide — continuidade e execução de duas horas

Base Git: `31040a0ae01ac93597095fa9af6c73772e5fa60c`. Release: `belial-a6-wide-2h-v1`. Instalação: `/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter/releases/belial-a6-wide-2h-v1`. Instalador e ZIP final: `/home/hobit/Downloads`. PAPER/shadow, `orders=false` em toda a versão.

Esta versão contém o snapshot completo de origem, suas dependências históricas e as alterações A6. O manifesto corrente é **A6_MANIFEST_SHA256.json**; os demais manifestos/resultados herdados são evidência histórica e NÃO validam esta versão. O ponto de entrada é `python3 -B -m a6.run`. Não executar os instaladores A1–A51 incluídos no histórico.

## O que executa

- Os 16 sensores Solana qualificados da linha anterior, com os mercados Hyperliquid/Lighter e o observador N2. As regras históricas permanecem um controle identificado, separado do novo laboratório.
- Binance: inventário dinâmico de contratos **USDT margined perpetual TRADING**, com validação de filtros oficiais. Recepção ampla de mini-tickers e mark/funding por WebSocket. Até 4.096 contratos no catálogo por proteção de memória, sem lista fixa de 16 símbolos. Esse teto não significa que existam 4.096 contratos disponíveis.
- Até 64 mercados Binance simultâneos com livros de 20 níveis e fluxo agregado de negócios. A seleção usa retornos causais, preserva posições abertas e reserva vagas rotativas para exploração. Reavaliação a cada 10 segundos, controle de assinaturas a cada cinco. A contagem solicitada não é prova de dados recebidos: o painel separa ambas.
- Descoberta Raydium: páginas de 100 pools, rodando pelas primeiras dez páginas ordenadas por volume de 24h; uma página a cada 15 segundos. Meta de até 1.000 registros por ciclo de aproximadamente 150 segundos mais latência, sujeitos à disponibilidade e sobreposição entre páginas vivas.
- DEX Screener: perfis recentes Solana a cada minuto e atualização rotativa de lotes de até 30 tokens; até 600 tokens retidos. Pools de protocolos retornados, como Meteora, Orca ou PumpSwap, são identificadas pelo endereço e protocolo. Sua presença efetiva depende da resposta da API. Não há cobertura exaustiva desses protocolos.
- Axiom: **sem integração direta**. Não foi verificada uma API pública documentada. Nenhum endpoint privado, cookie de sessão ou login é utilizado. Descoberta de mercados Solana por APIs documentadas não equivale a consumir o feed Axiom.

As pools DEX descobertas são **observação amostrada de APIs em cache**, não sensores on-chain novos de swaps nem rotas automáticas para short. Não são promovidas por coincidência de ticker. A expansão de oportunidades PAPER executáveis nesta entrega é o universo de perps Binance; a expansão Solana forma um catálogo de pesquisa. Não atribuir short ou lucro a uma pool spot sem instrumento adequado.

## Correções implementadas

1. N2 importa `a51.model_store`, usa contrato de código A6 e diretório `runtime/belial-a6/models`. Só publica sessões `m2-a6-*` completas e compatíveis, sem restaurar pesos A4.
2. Sinal com book temporariamente ausente pode esperar até dois segundos. Cada tentativa revalida integridade da fonte, janela, custos e book. O fill continua exigindo cotação nova após a latência. Mudança de fonte e expiração cancelam a espera. Replay determinístico coberto por teste.
3. Quotes reprovadas agora registram custos e limite por venue.
4. Relay RPC reutiliza conexões HTTPS num pool de quatro conexões. Mantém orçamento de 3 requisições/s e backoff/cooldown global; 401/403 e redirects não são contornados. Cache de transações positivas: 120 segundos. Contadores por método e tipo de erro.
5. Analysis usa cache de JSON invalidado por alteração de arquivo. O controlador atualiza contexto dos mercados afetados, incluindo mensagens de funding/contexto, em vez de percorrer todos os books a cada evento.
6. Cache nativo de resolução de atores limitado a 8.192 entradas por sensor. Aplicação de atores permanece ordenada; concorrência de enriquecimento por sensor não foi reescrita.
7. Treino OpenCL prevê somente a cabeça a atualizar, preservando a ordem SGD e a matemática do modelo. O teste no dispositivo compara a previsão individual com a previsão de 32 cabeças e verifica isolamento da atualização. Não há ganho de velocidade medido no PC ainda.
8. Freshness global da UI usa timestamps de estado/plataforma; heartbeat visual não renova dados de trading. Projeção conserva identificador, duração e campos de progresso quando presentes. A execução A6 tem cronômetro próprio de 120 minutos.
9. Painel adicional com universo amplo, motivos dos filtros, fontes, pools e gráfico de preço observado por candidato. O painel antigo continua acessível para Solana/N2. Não foram introduzidos dados ilustrativos na interface live.

## Hipótese de cauda Binance

É uma hipótese nova, sem vantagem demonstrada. Usa queda em 30/120 segundos, tendência de dez minutos quando disponível, fluxo vendedor observado por ao menos 45 segundos e profundidade executável. Não identifica wallets Binance nem afirma detectar intenção de participantes.

Parâmetros congelados em `a6/tails.py`: retornos máximos −12/−35 bps, fração vendedora mínima 60%, 20 negócios e 5.000 USDT de fluxo, limite de custo de 30 bps incluindo estresse de 3 bps; taxa **hipotética** de 5 bps por lado. Portfólio independente de 1.000 USDT, notional-alvo 100 USDT, máximo oito posições, latência simulada 300 ms. Não usa alavancagem.

Stop entre 75 e 300 bps conforme volatilidade causal; ativação de trailing ao menos 100 bps, giveback ao menos 50 bps, duração máxima de uma hora. Critérios são registrados antes da entrada. Não calibrados retrospectivamente para produzir lucro. Comportamento de financiamento ainda não é reconciliado como settlement: resultado é `net_ex_funding`; **all_in_net permanece null**, assim como custos externos não informados. Não somar Binance, cenários Solana ou políticas como uma carteira única.

A execução usa snapshots de top-20, profundidade suficiente, filtros de lote e nova cotação após a latência. Recusa dados antigos, duplicados, futuros, books cruzados ou insuficientes. Quedas de conexão/overflow cancelam entradas pendentes, limpam contexto correspondente e marcam caminhos de posições abertas como censurados. Posições abertas ao fim são censuradas, sem fechamento inventado. Liquidez oculta, RPI, impacto próprio, fila e taxas reais da conta não são reproduzidos.

O N2 antigo continua consultivo e com labels TOKEN/WSOL; a otimização de GPU não transforma seu alvo em PnL de perp. Os resultados econômicos do novo portfólio ficam em journal próprio. Um modelo treinado em labels econômicos, suporte nativo a novos protocolos/mints e promoção automática de pools spot para instrumentos verificados permanecem trabalhos posteriores, não recursos declarados como concluídos.

## Operação

Executar sem sudo:

```bash
bash "/home/hobit/Downloads/BELIAL_A6_WIDE_2H_INSTALAR.sh"
```

O instalador verifica conteúdo e testes, cria release separada, registra branch/commit num worktree separado e tenta publicar **essa branch**, sem merge/push na main. Preserva mudanças locais da checkout original. Em caso de falha de push, o commit e o caminho do worktree ficam em `runtime/belial-a6/GIT_CHECKPOINT.json`.

O serviço `belial-a6-2h.service` sobrevive ao fechamento do terminal. Aguarda processos de captura/gerenciamento A51R anteriores terminarem; não para o scan de nove horas. Depois verifica espaço livre de 6 GiB, GPU, testes e build Rust offline usando dependências já instaladas. Cargo usa até três jobs no i5 de quatro núcleos; o coletor amplo possui runtime Tokio de três workers. Não existem cotas artificiais de CPU/RAM no novo serviço. O arquivo `RESOURCE_SAMPLES.jsonl` registra, a cada cinco segundos, CPU e RSS dos processos filhos, memória e pressão do sistema, e status GPU disponível no N2; somas de RSS podem contar páginas compartilhadas mais de uma vez. A captura de 120 minutos começa após a preparação; não inclui espera/build.

Para acompanhar:

```bash
journalctl --user -u belial-a6-2h.service -f
```

Endereço do scanner é impresso como `A6_SCANNER_URL`. Ao terminar, gera `/home/hobit/Downloads/BELIAL_A6_2H_RESULTADO_*.zip`, contendo os resultados, `A6_SUMMARY.json`, `A6_DASHBOARD_FINAL.html`, journals, estados, validações e logs. Pode ficar grande por preservar a evidência original do PAPER. O inventário do ZIP é calculado após redação dos endpoints reconhecidos do Solana.

`--verify-only` valida o pacote sem instalar, Git ou captura. `--install-only` instala/versiona e não inicia o serviço. Para interromper apenas A6: `systemctl --user stop belial-a6-2h.service`. Uma interrupção/reboot não é preenchida como observação contínua: os dados ficam preservados e uma nova execução cria uma sessão nova; não há auto-resume de posições A6.

## Validação e limites

Ver `A6_LOCAL_VALIDATION.json` e logs em `validation_a6/`. Os testes locais exercitam código Python real com fixtures sintéticas, replay do controlador legado, causalidade, contratos, filtros, gates e transporte HTTPS com conexão instrumentada. Os testes nativos incluídos são executados pelo Cargo no PC. Rust/GPU não estavam disponíveis no ambiente de geração; não declarar compilação local, benchmark GPU local ou teste de mercado de duas horas já executado.

A cobertura live depende de disponibilidade dos provedores e da rede do usuário. Erro de acesso é registrado, sem proxy para contorná-lo. Mais hardware não garante mais eventos de mercado; os critérios de aceitação são cobertura efetiva, ausência de perda não registrada, latência observada, integridade do replay e PnL interpretável por cenário. Duas horas são um teste funcional de captura, não comprovação estatística de lucro em eventos raros.

Referências oficiais consultadas em 26/09/2026:

- https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market
- https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/public
- https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information
- https://docs.raydium.io/api-reference/api-v3-endpoints/pools/list-pools-with-pagination
- https://docs.dexscreener.com/api/reference
- https://docs.axiom.trade/llms.txt
