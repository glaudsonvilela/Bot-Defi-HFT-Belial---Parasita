# Belial A8 — instalação e teste PAPER

A8 é uma release isolada. Preserva A7, fontes históricas, resultados e configurações existentes. Não envia ordens reais. O arquivo `.sh` contém o código completo, os testes e o manifesto SHA-256.

## Executar no PC

Salve `BELIAL_A8_CROSSCHAIN_2H_INSTALAR.sh` em `/home/hobit/Downloads` e execute, sem sudo:

```bash
bash /home/hobit/Downloads/BELIAL_A8_CROSSCHAIN_2H_INSTALAR.sh
```

O instalador valida hashes e testes Python, faz backup dos checkpoints, instala uma release nova e cria commit/branch próprios no Git local. Tenta publicar essa branch no GitHub. Se o push falhar, informa a pendência e preserva o commit local. Não altera a branch principal.

Acompanhar:

```bash
journalctl --user -u belial-a8-2h.service -n 60 -f
```

O painel abre no navegador. O endereço atual aparece em `A8_PAINEL=`; URLs de execuções anteriores não servem. O painel é local e exibe seis carteiras independentes, entradas, saídas, PnL realizado, estimativa das posições abertas, gráficos e bloqueios por moeda.

O Rust é testado e compilado no seu PC antes da captura. Após seleção e início dos feeds, há preparação mínima de 120 segundos; ambas as redes devem permanecer saudáveis por 60 segundos. Também são necessários os 20 livros recentes, observação de preços de livro para todas as moedas e histórico utilizável em pelo menos 16 delas. A preparação aborta após 360 segundos se os critérios não forem atendidos. O restante das moedas continua explicitamente bloqueado enquanto faltar qualidade.

`A8_CAPTURA_2H_INICIADA=` confirma o início da janela. `Serviço iniciado` apenas confirma o lançamento do processo.

Após duas horas, `A8_ENTRADAS_ENCERRADAS` fecha a janela de novas entradas e cancela pendências. Se houver posições abertas, o coletor continua por **até mais 60 minutos**, mantendo as regras de saída. Termina antes quando todas forem fechadas. Posições ainda abertas no limite são registradas como sem desfecho completo; não são convertidas em lucro realizado.

Resultado: `/home/hobit/Downloads/BELIAL_A8_2H_RESULTADO_*.zip`. A finalização inclui replay e compactação; pode demorar após o cronômetro. O relatório estático `RELATORIO.html` permanece disponível, mesmo após o servidor do painel fechar.

Parar somente a A8, preservando a finalização:

```bash
systemctl --user stop belial-a8-2h.service
```

Não encerre processos com `kill -9`: isso impediria a finalização normal. A8 aguarda os scans A51R/A6/A7 reconhecidos; não os cancela.

## O que mudou

| Carteira PAPER | Fonte do preço do sinal | Entrada | Saída |
|---|---|---|---|
| CONTROLE | miniTicker | regra anterior | regra anterior |
| PRECO | último negócio aggTrade observado | regra anterior | regra anterior |
| LIVRO | ponto médio de livro válido | regra anterior | regra anterior |
| ENTRADA | ponto médio | confirmação de venda recorrente on-chain | regra anterior |
| SAIDA | ponto médio | regra anterior | saída por falha da tese |
| COMBINADO | ponto médio | confirmação on-chain | saída por falha da tese |

Compare PRECO e LIVRO com CONTROLE para investigar a fonte do preço. Compare ENTRADA, SAIDA e COMBINADO com LIVRO para investigar os filtros. Não some as carteiras como uma carteira única.

O ponto médio é uma observação de cotação, não preço garantido de negócio. Fills simulados continuam percorrendo os níveis do livro, com lotes, taxas, latência e revalidação. Nenhum preço é inventado durante lacunas. Mudanças de cotação podem introduzir ruído; é por isso que existem controles separados.

Os limiares anteriores de queda, pressão vendedora, custos, stop e trailing foram preservados. As moedas são selecionadas novamente no início: a lista continua com 20 contratos Binance e passa a priorizar pools qualificadas antes da profundidade e do volume. Se não houver 20 contratos elegíveis, a preparação para explicitamente. Se não houver 20 com pool qualificada, vagas restantes podem conter contratos válidos para os controles; os braços on-chain mostram `POOL_UNAVAILABLE`. A seleção não inventa mapeamentos ou promete 20 pools válidas.

Outras mudanças:

- RPC: método, HTTP, código JSON-RPC, latência, quantidade de endereços e tamanho do intervalo de blocos, sem registrar URL, token ou cabeçalho secreto.
- Recusas 401/403/451 interrompem o provedor. Não há rotação para contornar bloqueios.
- Reuso HTTPS limitado a duas conexões por host quando não há proxy configurado; proxies existentes são respeitados. Os limites de requisição permanecem.
- Histórico limpo após desconexão da sua fonte; preços antigos não ganham horário novo.
- Diagnóstico separado de rede indisponível, pool ausente, preço velho, referência temporal ausente e amostra insuficiente.
- Cobertura por moeda e descartes por pool/motivo nos arquivos finais. Cobertura temporal é estimada por ticks, não garantia entre observações.
- Saúde final congelada no horário do último evento, antes do replay; o relatório não envelhece os feeds artificialmente durante a finalização.
- Cache de características para evitar repetir cálculos iguais no mesmo instante, invalidado quando o histórico muda. Não altera a política de entrada.
- Rust registra o preço real do último negócio junto do bucket de fluxo existente; não requer conexão extra para isso.

## Investigação que orientou a mudança

O teste A7 terminou entre 26/09/2026 22:38:07 e 27/09/2026 00:38:07 de Brasília. A BNB falhou após 10,383 segundos; as 121 vendas líquidas aceitas eram Solana. A medição dos timestamps desse arquivo mostrou continuidade suficiente em aproximadamente 19,25% das avaliações para miniTicker, 19,11% para buckets de negócios e 89,26% para livros. Isso mede disponibilidade temporal, não retorno, previsão ou lucro. Os preços dos negócios não estavam arquivados nos buckets antigos e não foram reconstruídos.

A consulta pública feita no ambiente de desenvolvimento aceitou um filtro de nove pools BNB com resposta vazia válida; as chamadas levaram aproximadamente 6–7 segundos. Isso não reproduz a rede do seu PC, não identifica a causa da recusa antiga e não garante estabilidade. A8 fará a verificação sustentada no ambiente real.

## Limites e validação

Validações locais: 30 testes A8, 57 testes de regressão A7 e 71 A6; sintaxe JavaScript e renderização funcional por fixture DOM. Há teste com subprocesso real, ZIP final e replay, com relógio curto e feeds sintéticos. Testes de preparação, limpeza após desconexão, execução tardia proibida, saídas após o prazo e proteção do proxy foram incluídos.

O ambiente de desenvolvimento não tem Cargo nem navegador gráfico. O teste/compilação Rust são gates obrigatórios no PC; não foram apresentados como executados aqui. A inspeção visual em navegador e o novo teste de mercado permanecem pendentes. A GPU continua sem uso: não há modelo validado para este alvo.

Ter seis variantes ou melhor cobertura não prova rentabilidade. A8 mede se a hipótese de continuação da queda sobrevive a dados observáveis, custos e desfechos completos. Wallet vendedora recorrente não é identificação de HFT da Binance.

## Caminhos e continuidade

- Base: `/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter`
- Release: `releases/belial-a8-crosschain-2h-v1`
- Runtime: `runtime/belial-a8`
- Repositório: `/home/hobit/Área de trabalho/belial-github-publicar`
- Branch: `work/belial-a8-2h-<hash do pacote>`
- Serviço: `belial-a8-2h.service`
- Commit base preservado: `31040a0ae01ac93597095fa9af6c73772e5fa60c`
- Histórico A7: namespace `a7` preservado; novo código em `a8`, novo binário `belial-a8-feed`.

Apenas verificar o pacote, sem instalar nem iniciar:

```bash
bash /home/hobit/Downloads/BELIAL_A8_CROSSCHAIN_2H_INSTALAR.sh --verify-only
```

Para instalar e criar checkpoint sem iniciar a coleta, use `--install-only`. O comando normal inicia o teste automaticamente após as verificações. Endpoints explicitamente configurados por `BELIAL_SOLANA_RPC_URL`, `BELIAL_SOLANA_WS_URL` e `BELIAL_BNB_RPC_URL` continuam tendo precedência; não publique URLs com credenciais.

## Reprodução exploratória adicional

A8 reprocessou os 607.714 eventos antigos. Os oito eventos financeiros do CONTROLE (sinais, cancelamento, fills e posições abertas no fim) foram idênticos aos da A7. O novo painel e os motivos de diagnóstico têm campos adicionais.

| Variante | Entradas | Fechadas | Abertas | Realizado USDT | Estimativa abertas USDT |
|---|---:|---:|---:|---:|---:|
| CONTROLE | 2 | 0 | 2 | 0.000000 | -0.751298 |
| LIVRO | 5 | 2 | 3 | -0.115364 | -0.831390 |
| ENTRADA | 1 | 0 | 1 | 0.000000 | -0.163046 |
| SAIDA | 5 | 3 | 2 | -0.530245 | -0.387734 |
| COMBINADO | 1 | 0 | 1 | 0.000000 | -0.163046 |

PRECO/aggTrade não pôde ser avaliado nessa gravação: os buckets antigos não incluíam preço. Seus zeros não são resultado de estratégia. A variante de livro permitiu uma entrada com confirmação on-chain em ZEREBRO, ainda aberta e negativa ao fim. Mais observações e entradas não demonstraram rentabilidade. A BNB continuou indisponível conforme os eventos originais; não foram inventadas transações nem dados posteriores às duas horas. Esta reprodução foi feita na mesma amostra usada para investigar o problema e não substitui a nova janela PAPER.
