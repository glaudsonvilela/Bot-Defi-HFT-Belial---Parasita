# BELIAL A3 / RESEARCH TERMINAL / 90 minutos

A3 implementa correção do ciclo de vida N2, supervisão explícita, previsão prospectiva auditável, checkpoints compatíveis, WebSocket spot multiplexado e terminal de pesquisa integrado. **PAPER/shadow apenas.** Não existe envio de ordens ou carteira real. O visual é inspirado em terminais financeiros de alta densidade; não é software, dados ou afiliação Bloomberg.

## Executar

Salve `BELIAL_A3_90M_EXECUTAR.sh` em `/home/hobit/Área de trabalho/Altside` e execute como usuário normal:

```bash
bash "$HOME/Área de trabalho/Altside/BELIAL_A3_90M_EXECUTAR.sh"
```

Não execute com sudo nem em paralelo com outra captura. O instalador verifica hashes e testes, instala uma release separada e abre o painel. É necessário manter o computador ligado, SSD montado, terminal aberto e sem suspensão. A duração da captura é 90 minutos; testes, compilação, qualificação e finalização ficam fora desse prazo.

Não execute A2/FIX4 novamente para preparar esta versão. Não é necessário extrair o ZIP. Sem retomada automática das posições após queda de energia.

```text
Base: /mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter
Release: releases/belial-a3-90m-v1
Build nativo isolado: target-belial-a3
Sessão: evidence/m2-a3-<UTC>
Resultado: $HOME/Área de trabalho/Altside/BELIAL_A3_90M_RESULTADO_<UTC>.zip
```

`--verificar-pacote` extrai e valida o pacote e executa testes Python offline, sem instalar, sem GPU e sem mercado. Isso não é aprovação de build Rust.

## Terminal

F1 Mercado: 24 candidatos, sensor atual, livros, seleção de ativo, grupo CORE/EXPANSÃO, cenário ROTA/venues, política, curva do grupo e funil. A curva de carteira não é a cotação do ativo selecionado.

F2 Posições: abertas/fechadas, PnL ex-funding, líquido do modelo, all-in declarado, pico bruto, devolução bruta, stop e ativação real do trailing. MFE e limiar do trailing são brutos; não confundir com lucro líquido disponível. Custos externos desconhecidos permanecem indisponíveis.

F3 Agente: GPU, heartbeat, leitura dos três canais, última previsão, labels válidos/censurados, atualizações, horizontes, 32 cabeças, avaliação contra taxa-base e origem dos pesos. GPU disponível não é prova de aprendizado. Previsões são consultivas, não sinais de negociação.

F4 Fontes: componentes, sensor por ativo, relay HTTP, WebSocket compartilhado, conexões efetivas, falhas/reconexões, última mensagem, build e tempos. Dados sem atualização não ficam verdes artificialmente.

Atalhos F1–F4, seleção por clique e busca; pausa afeta apenas a tela. O botão de exportação salva o estado visível, não cria ordens. Tabelas podem rolar internamente. Horários explicitamente de Brasília.

## Escopo congelado

24 candidatos, 90 minutos, N2 8192 × 32 e as políticas 50/35, 75/50 e 100/75 mantidos. A3 não muda os thresholds para produzir mais entradas. Não amplia o universo. Capital de pesquisa e tamanho das posições permanecem os da A2: não se tornou automaticamente uma sessão de US$ 50. CORE e EXPANSÃO, políticas e cenários são carteiras contrafactuais separadas: nunca somar como um único capital.

O universo configurado não significa 24 sensores/perps aprovados. TransferHook e outros casos incompatíveis continuam em RAW/quarentena sem fabricar swaps ou sinais. Uma falha isolada não encerra livros ou painel independentes.

## Agente e checkpoints

Primeira sessão A3 começa sem pesos A1/A2. Ao terminar uma sessão A3 completa que tenha treinado, publica geração imutável com hashes de código/contrato, configuração e pesos. A próxima sessão compatível pode continuar os pesos e os contadores de treinamento; as métricas de avaliação são zeradas para o novo período. Não há publicação de sessão interrompida como checkpoint completo.

Reinicialização explícita, somente quando se decidir criar um novo experimento:

```bash
BELIAL_A3_MODEL_MODE=fresh bash "$HOME/Área de trabalho/Altside/BELIAL_A3_90M_EXECUTAR.sh"
```

Não editar pesos/metadados manualmente: hash divergente, contrato incompatível ou geração posterior ao início da sessão provoca recusa. Um crash do agente não reinicia silenciosamente o aprendizado no mesmo teste e não produz novos sinais; os outros componentes continuam e a falha fica visível.

## Conectividade e desempenho

Um WSS remoto para os sensores spot qualificados, com assinaturas individuais e redistribuição local; um segundo WSS pode ser utilizado pelo recorder RAW. Uma conexão de qualificação temporária pode existir na preparação. Cada assinatura `mentions` mantém somente um endereço; não se envia uma lista inválida de 24 endereços.

Reconexão upstream centralizada, espera exponencial e `Retry-After`; 401/403 não são contornados. Uma falha fecha as entregas dependentes, permitindo que leitores invalidem seu contexto. Fila lenta/velha não é reproduzida como dado atual. O relay não remove as cotas do provedor nem garante disponibilidade.

Interface sem frameworks, fontes externas ou animações decorativas; livros com alvo visual de até 25 Hz, painéis lentos até 4 Hz e renderização sincronizada ao navegador. A tela pode consolidar estados intermediários; deltas dos livros e registro de decisões não são descartados por esse mecanismo. Idade da fonte, tempos do transporte e tempo de renderização são métricas diferentes. Não existe promessa de latência zero.

## Finalização

O terminal exibe o estágio durante encerramento, replay, exportação e compactação. Após a janela não há novos fills; preserva-se a reconciliação de funding herdada. `RESULT_ZIP=` e `FINAL_DASHBOARD=` identificam as saídas reais. Inclua no diagnóstico `n2/AGENT_VALIDATION.json`: o sucesso exige evidência de previsões/labels/updates, não apenas inicialização OpenCL.

Consulte `VALIDACAO_LOCAL.md` para testes efetivamente executados e limitações.
