# Fontes primárias consultadas em 25/09/2026

- Solana `logsSubscribe`: https://solana.com/docs/rpc/websocket/logssubscribe — `mentions` contém um endereço por assinatura. As notificações têm id de assinatura, slot, signature, err e logs. O multiplexador mantém assinaturas separadas em uma conexão.
- Solana RPC público: https://solana.com/docs/references/clusters — disponibilidade/cotas podem mudar; 429 exige redução de solicitações e respeito a Retry-After; 403 não deve ser contornado por este software.
- MDN requestAnimationFrame: https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame — chamada antes da repintura e comportamento dependente do navegador/tela/visibilidade. O alvo de 25 Hz não é promessa de mercado a 25 Hz ou garantia de latência zero.

Os contratos de custos, Raydium e venues herdados permanecem nos documentos A2. Esta revisão não atualiza tarifas nem certifica listagem, identidade on-chain ou profundidade atual de 24 mercados. Fontes públicas documentam protocolos, não substituem os dados do teste.

O layout Belial A3 é original, inspirado no tipo de terminal financeiro de alta densidade solicitado. Não utiliza marca, API, feeds ou licença Bloomberg. Não constitui réplica de funcionalidades proprietárias.
