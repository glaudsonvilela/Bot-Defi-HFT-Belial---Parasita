# Migração A2 → A3

A3 usa uma nova árvore Python `a3/`, novo painel e novos testes. As árvores A1/A2, leitores Rust, arquivos N2/heads, regras e configurações originais continuam no pacote para rastreabilidade; 157 arquivos têm hashes de preservação.

Mudanças intencionais: supervisão e inicialização de inputs, causalidade de publicação N2, memória versionada, transporte WS local/compartilhado, apresentação limitada de estado, progressos de finalização. Isso altera a disponibilidade de observações e a definição de instante do forecast: A3 é novo experimento, não promessa de reproduzir os resultados numéricos A2.

Novos campos das posições são metadados de exibição do limiar original. Nos testes, campos econômicos originais de abertura/quantidade/saída/taxas/funding/PnL permanecem iguais; não se alega igualdade byte a byte entre uma linha A2 sem esses campos e a linha A3. O replay dentro da A3 conserva identidade de hashes nas fixtures executadas.

O servidor da UI só aceita leitura local. Consumidor de apresentação lento não deve bloquear o PAPER. As projeções retêm posições abertas e limitam histórico exibido/curvas/últimas previsões; a evidência integral continua em registros próprios.

Nativo: `engine_a2/` continua intacto. Os binários são testados/compilados offline/locked por componente em target-belial-a3. A compilação histórica A2 no computador do usuário não é prova de integração do relay Python A3 com a rede pública. Esta etapa ainda é validada no computador de destino. GPU e disponibilidade dos RPCs não foram simuladas como aprovadas.

Não elimina extensão Token-2022 bloqueada; não abre ordens reais; não altera sizing para US$ 50; não prolonga a janela de 90 minutos; não mata ou substitui uma sessão em andamento; não recupera posições após falta de energia.
